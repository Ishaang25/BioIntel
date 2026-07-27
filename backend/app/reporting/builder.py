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
from app.utils.text import truncate

log = get_logger(__name__)

CITATION_RE = re.compile(r"\[([CE])(\d+)\]")

#: The memo structure.  Order and headings are fixed so that two BioIntel
#: reports can be compared side by side, which is how IC packs are read.
SECTION_PLAN: tuple[dict[str, str], ...] = (
    {
        "id": "thesis",
        "heading": "Scientific Thesis",
        "instruction": (
            "State, in the company's own terms, what scientific proposition the investment "
            "rests on: the target, the mechanism, the modality, the indication, and why the "
            "company believes it will work. Cite the claims that constitute the thesis."
        ),
    },
    {
        "id": "evidence_base",
        "heading": "Evidence Base and Its Limits",
        "instruction": (
            "Characterise the evidence the deck actually presents: which tiers, which model "
            "systems, what statistical support. Distinguish claims backed by the company's own "
            "data from claims backed only by citation or by assertion. Be explicit about what "
            "the deck does not show."
        ),
    },
    {
        "id": "literature",
        "heading": "External Literature Assessment",
        "instruction": (
            "Summarise what the retrieved literature and trial registry records establish about "
            "this target, mechanism and indication, independent of the company. Identify the "
            "strongest corroborating evidence and the strongest disconfirming evidence, citing "
            "specific records."
        ),
    },
    {
        "id": "contradictions",
        "heading": "Contradictions and Unsupported Claims",
        "instruction": (
            "Give each material contradiction its own paragraph: what the company claims, what "
            "the conflicting record shows, and how serious the conflict is. Then list the "
            "thesis-critical claims for which no external corroboration was found, stating "
            "clearly that this is absence of evidence rather than evidence of absence."
        ),
    },
    {
        "id": "translational",
        "heading": "Translational Risk",
        "instruction": (
            "Assess the distance between the evidence presented and the clinical claim implied: "
            "species, dose, endpoint, patient population, and the precedent for this class of "
            "translation succeeding. Reference the trial registry evidence where relevant."
        ),
    },
    {
        "id": "competitive",
        "heading": "Competitive and Precedent Landscape",
        "instruction": (
            "Using the retrieved trial records and literature, describe who else has worked on "
            "this target or mechanism and what happened. Note terminated or withdrawn trials "
            "explicitly. If the retrieval found no precedent, say so and discuss what that means."
        ),
    },
    {
        "id": "scorecard",
        "heading": "Scorecard and What Drives It",
        "instruction": (
            "Explain the ten-dimension scorecard using the supplied figures: which dimensions "
            "carry the case, which hold it back, and what would move each. Distinguish "
            "dimensions that score low because evidence disagrees from those that score low "
            "because claims could not be checked -- these have completely different remedies. "
            "Do not recompute any number and do not reproduce the table; the memo renders it "
            "separately. Explain what it means."
        ),
    },
    {
        "id": "verification",
        "heading": "Regulatory and Registry Verification",
        "instruction": (
            "Report what was checked against authoritative sources (the FDA drug database and "
            "ClinicalTrials.gov) and what those checks returned. State clearly which claims "
            "were confirmed, which were refuted by the public record, and which could not be "
            "checked and why -- a source that does not cover a product class is a coverage "
            "gap, not a negative finding. This section tells the reader which parts of the "
            "regulatory narrative rest on company assertion alone."
        ),
    },
    {
        "id": "diligence",
        "heading": "Recommended Diligence",
        "instruction": (
            "Set out the diligence programme: the questions to put to the company, the data "
            "rooms to request, and the external experts worth consulting. Group by what each "
            "would resolve. Reference the question list supplied rather than inventing new ones."
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
                questions=_format_questions(questions),
                section_plan=_format_section_plan(),
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
            executive_summary=output.executive_summary,
            sections=sections,
            recommendation=output.recommendation,
            limitations=_dedupe_strings(limitations),
            citations=references.citations(sorted(used_refs, key=_ref_sort_key)),
            overall_score=overall.score,
            overall_band=overall.band,
            confidence=overall.confidence,
            score_breakdown=overall.breakdown,
            invalid_citations=invalid,
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
        f"{index + 1}. **{item['heading']}** — {item['instruction']}"
        for index, item in enumerate(SECTION_PLAN)
    )


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


def _format_questions(questions: list[Question]) -> str:
    if not questions:
        return "(no diligence questions were generated)"
    return "\n".join(
        f"{index + 1}. [{q.priority.value}/{q.category.value}] {q.question}"
        f"\n    why: {truncate(q.rationale, 260)}"
        f"\n    good answer: {truncate(q.what_good_looks_like, 260)}"
        for index, q in enumerate(questions)
    )


def _ref_sort_key(ref: str) -> tuple[str, int]:
    match = re.match(r"([CE])(\d+)", ref)
    if not match:
        return (ref, 0)
    return (match.group(1), int(match.group(2)))


def _dedupe_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        key = value.strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(value.strip())
    return out


def _value(enum_or_str: Any) -> str:
    return enum_or_str.value if hasattr(enum_or_str, "value") else str(enum_or_str or "")
