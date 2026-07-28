"""Stage 10: assemble the Investment Committee memo.

The memo is generated section by section against a fixed plan, then validated:
every ``[C#]``/``[E#]`` citation the model emits is resolved against the real
reference table.  Unresolvable citations are stripped and recorded as a
limitation rather than shipped to an investment committee.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.analysis.questions import Question, Risk
from app.analysis.scoring import OverallScore
from app.core.config import settings
from app.core.enums import AssertionBasis, CredibilityBand
from app.core.logging import get_logger
from app.llm import prompts
from app.llm.client import LLMClient
from app.llm.schemas import ReportOut
from app.reporting.narrative import (
    confidence_reasons,
    dimension_narratives,
    evidence_ledger,
    rank_questions,
    recommendation_drivers,
)
from app.utils.dedupe import dedupe_strings
from app.utils.text import truncate

log = get_logger(__name__)

CITATION_RE = re.compile(r"\[([CE])(\d+)\]")

#: The memo structure.  Order and headings are fixed so that two BioIntel
#: reports can be compared side by side, which is how IC packs are read.
#:
#: Each section declares what it **owns** and what it **must not repeat**.
#: Without that, adjacent sections converge: measured on the CRISPR memo,
#: "Evidence Base" and "Contradictions" cited 32 of the same 42 claims
#: (Jaccard 0.76), restating the same findings under different headings. An
#: analyst reading four sections should learn four things.
SECTION_PLAN: tuple[dict[str, str], ...] = (
    {
        "id": "thesis",
        "heading": "Scientific Thesis",
        "owns": "what the company is asserting",
        "instruction": (
            "State, in the company's own terms, what scientific proposition the investment "
            "rests on: the target, the mechanism, the modality, the indication, and why the "
            "company believes it will work. Cite the claims that constitute the thesis. "
            "OWNS: the company's argument. DO NOT evaluate it here, do not cite external "
            "records, and do not mention verification status -- later sections do that."
        ),
    },
    {
        "id": "evidence_base",
        "heading": "Evidence Base and Its Limits",
        "owns": "what data the deck itself puts on the table",
        "instruction": (
            "Characterise the evidence the DECK presents: which tiers, which model systems, "
            "what statistical support, what sample sizes and comparators are stated or "
            "missing. Distinguish claims backed by the company's own data from claims backed "
            "only by citation or assertion. "
            "OWNS: the internal evidence inventory and its methodological gaps. "
            "DO NOT: discuss external literature, name individual contradictions, or argue "
            "about species-to-human translation. Those are the next three sections. If a "
            "claim's problem is that nobody outside the company can check it, name the "
            "pattern here and leave the specific claims to 'Open and Contested Claims'."
        ),
    },
    {
        "id": "literature",
        "heading": "External Literature Assessment",
        "owns": "what independent science says about this biology",
        "instruction": (
            "Summarise what the retrieved literature and registry records establish about this "
            "target, mechanism and indication INDEPENDENTLY of the company. Identify the "
            "strongest corroborating and strongest disconfirming records, citing [E#] ids. "
            "OWNS: the external scientific picture. "
            "DO NOT: re-describe the deck's own data, or restate claim-by-claim verification "
            "status. Where external science agrees or disagrees with the thesis in general "
            "terms, say so once, here."
        ),
    },
    {
        "id": "contradictions",
        "heading": "Open and Contested Claims",
        "owns": "the specific claims an analyst must resolve",
        "instruction": (
            "A short, ranked list -- not an essay. Cover ONLY claims that are (a) contradicted "
            "by evidence, or (b) thesis-critical AND unverified. Ignore everything else. "
            "For each: one line on what the company claims, one on what the record shows or "
            "why nothing could be found, and one on what document would settle it. "
            "OWNS: the specific unresolved items. "
            "DO NOT: repeat the evidence-tier discussion, re-summarise the literature, or "
            "include claims that are already corroborated. Keep this under 300 words; a long "
            "list here means the ranking was not applied."
        ),
    },
    {
        "id": "translational",
        "heading": "Translational Risk",
        "owns": "the gap between the biology shown and the clinic implied",
        "instruction": (
            "Biology only: species, dose, exposure, endpoint surrogacy, patient population, "
            "and the precedent for this class of translation succeeding or failing. "
            "OWNS: the biological argument for why this may not reproduce in humans. "
            "DO NOT: restate which claims were unverified, re-list evidence tiers, or repeat "
            "the contradiction list. If the translational risk is genuinely low, say so "
            "briefly rather than manufacturing concern."
        ),
    },
    {
        "id": "competitive",
        "heading": "Competitive and Precedent Landscape",
        "owns": "who else has tried this, and what happened",
        "instruction": (
            "Using the retrieved trial records and literature, describe who else has worked on "
            "this target or mechanism and what happened. Note terminated or withdrawn trials "
            "explicitly. If retrieval found no precedent, say so and discuss what that means. "
            "OWNS: third-party programmes and their outcomes. "
            "DO NOT: re-argue this company's own translational risk or evidence quality."
        ),
    },
    {
        "id": "scorecard",
        "heading": "Reading the Scorecard",
        "owns": "what the computed numbers mean for the decision",
        "instruction": (
            "The memo renders the scorecard table and its driver bullets separately -- do not "
            "reproduce either. Write the interpretation an analyst cannot get from the table: "
            "which two or three dimensions actually carry this decision, which are noise at "
            "this stage, and where the archetype weighting makes a difference. Distinguish "
            "dimensions scored low because evidence disagrees from those held near neutral "
            "because nothing could be checked -- different remedies entirely. "
            "OWNS: interpretation and prioritisation of the dimensions. "
            "DO NOT: recompute, re-round or re-list the numbers, or restate the driver bullets. "
            "Under 250 words."
        ),
    },
    {
        "id": "verification",
        "heading": "Regulatory and Registry Verification",
        "owns": "what authoritative sources returned",
        "instruction": (
            "Report what was checked against the FDA drug database and ClinicalTrials.gov and "
            "what those checks returned: confirmed, refuted, or not covered. A source that "
            "does not index a product class is a coverage gap, not a negative finding -- say "
            "which it was. "
            "OWNS: the authoritative-source audit trail. "
            "DO NOT: repeat the literature assessment or re-list unverified claims in general; "
            "confine this to what a regulator or registry actually said."
        ),
    },
    {
        "id": "diligence",
        "heading": "Recommended Diligence",
        "owns": "what to do next, in order",
        "instruction": (
            "The memo renders the ranked top five questions separately -- do not reproduce "
            "them. Write the programme around them: which data rooms to request, which "
            "external experts to consult, what sequence, and what each step would resolve. "
            "OWNS: the plan of action. "
            "DO NOT: restate the questions verbatim or invent new ones."
        ),
    },
)


@dataclass(slots=True)
class ReferenceTable:
    """Maps the ``[C#]``/``[E#]`` ids used in prompts to real records."""

    claims: dict[str, dict[str, Any]] = field(default_factory=dict)
    evidence: dict[str, dict[str, Any]] = field(default_factory=dict)

    def resolve(self, ref: str) -> dict[str, Any] | None:
        return self.claims.get(ref) or self.evidence.get(ref)

    def citations(self, refs: list[str]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for ref in refs:
            record = self.resolve(ref)
            if record is not None:
                out.append({"ref": ref, **record})
        return out


@dataclass(slots=True)
class BuiltReport:
    title: str
    executive_summary: str
    sections: list[dict[str, Any]]
    recommendation: str
    limitations: list[str]
    citations: list[dict[str, Any]]
    overall_score: float
    overall_band: CredibilityBand
    confidence: float
    score_breakdown: dict[str, Any]
    invalid_citations: list[str] = field(default_factory=list)
    #: Computed explanations, rendered by BioIntel rather than written by the
    #: model, so an explanation can never contradict the number it explains.
    dimension_narratives: list[dict[str, Any]] = field(default_factory=list)
    confidence_reasons: list[str] = field(default_factory=list)
    recommendation_drivers: list[str] = field(default_factory=list)
    top_questions: list[dict[str, Any]] = field(default_factory=list)
    evidence_ledger: dict[str, Any] = field(default_factory=dict)


class ReportBuilder:
    def __init__(self, llm: LLMClient) -> None:
        self.llm = llm

    async def build(
        self,
        *,
        company_context: str,
        company_name: str | None,
        overall: OverallScore,
        claim_summaries: list[dict[str, Any]],
        risks: list[Risk],
        questions: list[Question],
        references: ReferenceTable,
        extra_limitations: list[str] | None = None,
        scorecard: Any = None,
        scientific_assessment: Any = None,
    ) -> BuiltReport:
        scorecard_text = _format_scorecard(overall, claim_summaries, scorecard)

        # Computed once, up front: the memo's explanations and the prompt's
        # framing are then built from the same arithmetic, so the narrative
        # cannot drift from the scorecard it describes.
        ledger = evidence_ledger(claim_summaries)
        narratives = dimension_narratives(scorecard, claim_summaries)
        reasons = confidence_reasons(ledger, scorecard, extra_limitations)
        drivers = recommendation_drivers(scorecard, ledger, overall.score)
        ranked = rank_questions(questions, claim_summaries)

        output: ReportOut = await self.llm.structured(
            purpose="report",
            stage="report",
            system=prompts.system(),
            user=prompts.render(
                "report",
                company_context=company_context or "(no company profile could be extracted)",
                scorecard=scorecard_text,
                scientific_assessment=_format_scientific_assessment(scientific_assessment),
                claims=_format_claims_with_evidence(claim_summaries),
                risks=_format_risks(risks),
                questions=_format_ranked_questions(ranked),
                section_plan=_format_section_plan(),
                evidence_ledger=_format_ledger(ledger, reasons, drivers),
                section_claims=_format_section_routing(claim_summaries),
            ),
            schema=ReportOut,
            model=settings.model_reasoning,
            max_output_tokens=max(settings.llm_max_output_tokens, 24_000),
            context={
                "company_name": company_name,
                "claim_count": len(claim_summaries),
                "evidence_count": len(references.evidence),
                "supported_claims": sum(
                    1 for c in claim_summaries if c.get("supporting_count", 0) > 0
                ),
                "contradicted_claims": sum(
                    1 for c in claim_summaries if c.get("contradicting_count", 0) > 0
                ),
                "unsupported_claims": sum(
                    1 for c in claim_summaries if c.get("supporting_count", 0) == 0
                ),
                "overall_score": overall.score,
                "section_plan": list(SECTION_PLAN),
            },
        )

        sections, used_refs, invalid = self._validate_sections(output, references)
        limitations = list(output.limitations)
        limitations.extend(extra_limitations or [])
        if invalid:
            limitations.append(
                f"{len(invalid)} citation reference(s) generated by the model could not be "
                "resolved to a retrieved record and were removed from the memo text."
            )

        return BuiltReport(
            title=output.title or f"Scientific Due Diligence — {company_name or 'Unnamed company'}",
            executive_summary=_render_executive_summary(output.executive_summary),
            sections=sections,
            recommendation=output.recommendation,
            limitations=dedupe_strings(limitations, case_sensitive=False),
            citations=references.citations(sorted(used_refs, key=_ref_sort_key)),
            overall_score=overall.score,
            overall_band=overall.band,
            confidence=overall.confidence,
            score_breakdown=overall.breakdown,
            invalid_citations=invalid,
            dimension_narratives=[n.to_dict() for n in narratives],
            confidence_reasons=reasons,
            recommendation_drivers=drivers,
            top_questions=[
                {
                    "rank": index + 1,
                    "question": question.question,
                    "priority": _value(question.priority),
                    "category": _value(question.category),
                    "why_it_matters": why,
                    "what_good_looks_like": question.what_good_looks_like,
                    "claim_ids": list(question.claim_ids),
                }
                for index, (question, why) in enumerate(ranked)
            ],
            evidence_ledger=ledger,
        )

    def _validate_sections(
        self, output: ReportOut, references: ReferenceTable
    ) -> tuple[list[dict[str, Any]], set[str], list[str]]:
        """Strip citations that do not resolve; record them as defects."""
        sections: list[dict[str, Any]] = []
        used: set[str] = set()
        invalid: list[str] = []

        planned = {item["heading"].lower(): item for item in SECTION_PLAN}

        for index, section in enumerate(output.sections):
            body = section.body_markdown
            section_invalid: list[str] = []
            for match in CITATION_RE.finditer(body):
                bare = f"{match.group(1)}{match.group(2)}"
                if references.resolve(bare) is None:
                    section_invalid.append(bare)
                else:
                    used.add(bare)
            invalid.extend(section_invalid)

            if section_invalid:
                body = CITATION_RE.sub(
                    lambda m: (
                        m.group(0)
                        if references.resolve(f"{m.group(1)}{m.group(2)}") is not None
                        else ""
                    ),
                    body,
                )

            plan = planned.get(section.heading.lower())
            sections.append(
                {
                    "id": (plan or {}).get("id", f"section_{index + 1}"),
                    "heading": section.heading,
                    "body_markdown": body.strip(),
                    # The most-read line in each section: its bottom line for an IC.
                    "so_what": getattr(section, "so_what", ""),
                    "confidence": _value(getattr(section, "confidence", "")),
                    "confidence_reason": getattr(section, "confidence_reason", ""),
                    "citations": [f"{m.group(1)}{m.group(2)}" for m in CITATION_RE.finditer(body)],
                    "basis": AssertionBasis.INFERRED.value,
                    "order": index,
                }
            )

        if invalid:
            log.warning("report.invalid_citations", refs=sorted(set(invalid))[:20])
        return sections, used, sorted(set(invalid))


# --------------------------------------------------------------- formatting ---
def _format_section_plan() -> str:
    return "\n".join(
        f"{index + 1}. **{item['heading']}** (owns: {item.get('owns', '—')})\n   "
        f"{item['instruction']}"
        for index, item in enumerate(SECTION_PLAN)
    )


def _render_executive_summary(summary: Any) -> str:
    """Lay the structured summary out as the one page an IC reads.

    Rendered here rather than asked for as prose: the free-text version came
    back as a single 280-word paragraph, which is complete and unusable.
    """
    if isinstance(summary, str):  # defensive: older payloads
        return summary.strip()

    lines = [summary.investment_thesis.strip(), ""]

    if summary.key_strengths:
        lines.append("**What is established**")
        lines.append("")
        lines.extend(f"- {item.strip()}" for item in summary.key_strengths)
        lines.append("")
    if summary.key_risks:
        lines.append("**What is at risk or unresolved**")
        lines.append("")
        lines.extend(f"- {item.strip()}" for item in summary.key_risks)
        lines.append("")
    if summary.recommendation_line:
        lines.extend([f"**Recommendation.** {summary.recommendation_line.strip()}", ""])
    if summary.diligence_priorities:
        lines.append("**Do these three things first**")
        lines.append("")
        lines.extend(
            f"{index}. {item.strip()}"
            for index, item in enumerate(summary.diligence_priorities, start=1)
        )
        lines.append("")

    return "\n".join(lines).strip()


def _format_section_routing(summaries: list[dict[str, Any]]) -> str:
    """Tell each section which claims are its own.

    Sections converge when they are all handed the same claim list and asked
    to write something different about it. Routing the claims removes the
    temptation: 'Open and Contested Claims' is given its shortlist, and every
    other section is told to leave that shortlist alone.
    """
    contested = [
        claim
        for claim in summaries
        if claim.get("contradicted")
        or (
            claim.get("is_thesis_critical")
            and claim.get("evidence_state") in ("plausible_unverified", "company_reported")
        )
    ]
    contested.sort(
        key=lambda c: (bool(c.get("contradicted")), float(c.get("importance") or 0.0)),
        reverse=True,
    )
    audit = [c for c in summaries if c.get("requires_audit")]

    lines = [
        "These are the ONLY claims the 'Open and Contested Claims' section may discuss "
        "individually. Every other section must refer to them in aggregate, if at all.",
        "",
    ]
    if contested:
        for claim in contested[:12]:
            kind = (
                "CONTRADICTED"
                if claim.get("contradicted")
                else f"thesis-critical, {claim.get('evidence_state')}"
            )
            lines.append(f"  [{claim['ref']}] ({kind}) {truncate(claim['statement'], 140)}")
    else:
        lines.append(
            "  (none — no claim is contradicted and no thesis-critical claim is unverified. "
            "Say so in one line and keep that section very short.)"
        )

    if audit:
        lines.extend(
            [
                "",
                f"{len(audit)} claim(s) are company-reported metrics requiring audit. Refer to "
                "them as company assertions awaiting audit, never as scientific weaknesses:",
            ]
        )
        lines.extend(f"  [{c['ref']}] {truncate(c['statement'], 120)}" for c in audit[:8])
    return "\n".join(lines)


def _format_ledger(ledger: dict[str, Any], reasons: list[str], drivers: list[str]) -> str:
    """The counted evidence facts, so the memo quotes rather than estimates."""
    lines = [
        "Counted from the evidence graph. Use these figures verbatim; do not recount.",
        "",
        f"  claims scored: {ledger['claims_scored']} "
        f"(thesis-critical: {ledger['thesis_critical']})",
        f"  externally verified: {ledger['verified']} "
        f"| partially verified: {ledger['partially_verified']}",
        f"  unverified but uncontradicted: {ledger['unverified']}",
        f"  company-reported, awaiting audit: {ledger['company_reported']}",
        f"  contradicted: {ledger['contradicted']} "
        f"(thesis-critical: {ledger['thesis_contradicted']})",
        f"  verification coverage: {ledger['verification_coverage']:.0%}",
        "",
        "Why assessment confidence is what it is (state these, do not invent others):",
    ]
    lines.extend(f"  - {reason}" for reason in reasons)
    lines.extend(["", "The recommendation was driven by:"])
    lines.extend(f"  - {driver}" for driver in drivers)
    return "\n".join(lines)


def _format_ranked_questions(ranked: list[tuple[Any, str]]) -> str:
    if not ranked:
        return "(no diligence questions were generated)"
    lines = [
        "Ranked by expected impact on the decision. The memo renders these separately; "
        "use them to build the diligence programme rather than restating them.",
        "",
    ]
    for index, (question, why) in enumerate(ranked, start=1):
        lines.append(f"{index}. [{_value(question.priority)}] {question.question}")
        lines.append(f"     impact: {why}")
        lines.append(f"     good answer: {truncate(question.what_good_looks_like, 200)}")
    return "\n".join(lines)


def _format_scorecard(
    overall: OverallScore, claims: list[dict[str, Any]], scorecard: Any = None
) -> str:
    lines: list[str] = []

    if scorecard is not None:
        lines.extend(
            [
                f"Company archetype (drives dimension weighting): {scorecard.archetype.value}",
                f"Overall IC score: {scorecard.overall_score:.1f}/100 "
                f"({scorecard.overall_band.value})",
                f"Assessment confidence: {scorecard.overall_confidence:.2f}",
                f"Recommendation: {scorecard.recommendation.value}",
                f"Recommendation rationale: {scorecard.recommendation_rationale}",
                "",
                "Dimensions:",
            ]
        )
        for dimension in scorecard.dimensions:
            if not dimension.assessed:
                lines.append(f"  - {dimension.label}: NOT ASSESSED. {dimension.rationale}")
                continue
            lines.append(
                f"  - {dimension.label}: {dimension.score:.1f}/100 "
                f"({dimension.band.value}, confidence {dimension.confidence_band.value}) "
                f"- {dimension.rationale}"
            )
            for driver in dimension.negative_drivers[:2]:
                lines.append(f"      down: [{driver.get('claim_id')}] {driver.get('reason', '')}")
            for driver in dimension.positive_drivers[:2]:
                lines.append(f"      up:   [{driver.get('claim_id')}] {driver.get('reason', '')}")
        lines.append("")

    lines.extend(
        [
            f"Claim-level credibility roll-up: {overall.score:.1f}/100 ({overall.band.value})",
            "",
            "Corroboration outcomes across scored claims:",
        ]
    )
    statuses: dict[str, int] = {}
    for claim in claims:
        key = str(claim.get("corroboration_status") or "unknown")
        statuses[key] = statuses.get(key, 0) + 1
    for status, count in sorted(statuses.items(), key=lambda kv: -kv[1]):
        lines.append(f"  - {status.replace('_', ' ')}: {count} claim(s)")

    lines.extend(["", "Score components (computed, do not recompute):"])
    for key, value in overall.breakdown.items():
        lines.append(f"  - {key}: {value}")
    return "\n".join(lines)


def _format_scientific_assessment(assessment: Any) -> str:
    if assessment is None:
        return "(scientific assessment was not produced for this run)"
    precedent = assessment.modality_precedent
    failures = (
        f"Notable failures: {'; '.join(precedent.notable_failures)}"
        if precedent.notable_failures
        else "Notable failures: none surfaced by the retrieval."
    )
    return "\n".join(
        [
            f"Biological plausibility ({assessment.plausibility_confidence.value} confidence): "
            f"{assessment.biological_plausibility}",
            f"Modality precedent - {precedent.modality}; approved precedent exists: "
            f"{precedent.has_approved_precedent}. {precedent.precedent_summary}",
            failures,
            f"First-in-class assessment: {assessment.first_in_class}",
            f"Differentiation: {assessment.differentiation}",
            f"De-risking achieved: {assessment.de_risking_achieved}",
            f"Partnerability: {assessment.partnerability}",
            f"Milestones that matter: {'; '.join(assessment.milestones_that_matter)}",
            f"Key failure mode: {assessment.key_failure_mode}",
        ]
    )


def _format_claims_with_evidence(summaries: list[dict[str, Any]]) -> str:
    if not summaries:
        return "(no claims were extracted from this document)"
    blocks: list[str] = []
    for claim in summaries:
        lines = [
            f"[{claim['ref']}] {claim['statement']}",
            f"    quoted from page {claim.get('page_number')}: "
            f'"{truncate(claim.get("quote", ""), 240)}"',
            f"    type={claim.get('claim_type')} | category={claim.get('category')} "
            f"| corroboration={claim.get('corroboration_status')} "
            f"| deck evidence tier={claim.get('claimed_tier')} "
            f"| thesis-critical={claim.get('is_thesis_critical')} "
            f"| credibility={claim.get('credibility_score')}/100 ({claim.get('band')}) "
            f"| confidence={claim.get('confidence')}",
        ]
        if claim.get("verdict"):
            lines.append(f"    BioIntel verdict: {truncate(claim['verdict'], 500)}")
        if claim.get("key_uncertainties"):
            lines.append(f"    uncertainties: {'; '.join(claim['key_uncertainties'][:3])}")
        for evidence in claim.get("top_evidence", [])[:5]:
            lines.append(
                f"      [{evidence['ref']}] ({evidence['stance']}, strength "
                f"{evidence.get('strength')}) {evidence['citation']}"
            )
            if evidence.get("quote"):
                lines.append(f'          "{truncate(evidence["quote"], 220)}"')
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def _format_risks(risks: list[Risk]) -> str:
    if not risks:
        return "(no risks were identified)"
    return "\n".join(
        f"- [{r.severity.value}/{r.category.value}] {r.title}"
        + (" (deterministic rule)" if r.is_rule_based else "")
        + f"\n    {truncate(r.description, 400)}"
        for r in risks
    )


def _ref_sort_key(ref: str) -> tuple[str, int]:
    match = re.match(r"([CE])(\d+)", ref)
    if not match:
        return (ref, 0)
    return (match.group(1), int(match.group(2)))


def _value(enum_or_str: Any) -> str:
    return enum_or_str.value if hasattr(enum_or_str, "value") else str(enum_or_str or "")
