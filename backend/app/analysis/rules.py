"""Deterministic diligence rules.

These fire from computed facts, not from model judgement.  Two reasons they
exist alongside the model-generated risk register:

1. **Guaranteed coverage.** An unverifiable quote, a retracted citation or a
   terminated trial at the same target must surface every single time, not
   whenever the model happens to mention it.
2. **Auditability.** Each rule states its trigger, so a reviewer can check the
   finding against the data rather than against a narrative.

Rules never *lower* a score on their own -- scoring is handled in
:mod:`app.analysis.scoring`.  They raise flags.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from app.analysis.adjudicator import ClaimAdjudication
from app.analysis.scoring import ClaimScore, ClaimScoringInput
from app.core.enums import (
    ClaimCategory,
    EvidenceTier,
    QuoteVerification,
    RiskCategory,
    RiskSeverity,
)
from app.evidence.clinicaltrials import NEGATIVE_STATUSES
from app.utils.text import truncate


@dataclass(slots=True)
class RuleFinding:
    rule_id: str
    title: str
    category: RiskCategory
    severity: RiskSeverity
    description: str
    claim_id: str | None = None
    evidence_ids: list[str] = field(default_factory=list)
    source_pages: list[int] = field(default_factory=list)


@dataclass(slots=True)
class ClaimContext:
    """Everything the rules need to evaluate a single claim."""

    claim_id: str
    statement: str
    page_number: int
    scoring: ClaimScoringInput
    score: ClaimScore
    adjudication: ClaimAdjudication | None
    quote_match_score: float = 1.0
    from_visual: bool = False


#: Tiers that cannot by themselves support a claim about clinical benefit.
_PRECLINICAL_TIERS = {
    EvidenceTier.NONE_STATED,
    EvidenceTier.LITERATURE_ONLY,
    EvidenceTier.IN_SILICO,
    EvidenceTier.IN_VITRO,
    EvidenceTier.IN_VIVO_ANIMAL,
    EvidenceTier.EX_VIVO_HUMAN,
}

_CLINICAL_CATEGORIES = {ClaimCategory.CLINICAL_EFFICACY, ClaimCategory.SAFETY}


def evaluate(contexts: Iterable[ClaimContext]) -> list[RuleFinding]:
    """Run every rule over every claim and return the findings."""
    findings: list[RuleFinding] = []
    contexts = list(contexts)

    for context in contexts:
        findings.extend(_claim_rules(context))

    findings.extend(_portfolio_rules(contexts))
    return _dedupe(findings)


# ------------------------------------------------------------- claim rules ---
def _claim_rules(ctx: ClaimContext) -> list[RuleFinding]:
    out: list[RuleFinding] = []
    claim = ctx.scoring
    snippet = truncate(ctx.statement, 200)

    if claim.is_thesis_critical and ctx.score.contradicting_count > 0:
        out.append(
            RuleFinding(
                rule_id="contradicted_thesis_claim",
                title="Thesis-critical claim conflicts with published evidence",
                category=RiskCategory.SCIENTIFIC,
                severity=RiskSeverity.CRITICAL,
                description=(
                    f'The claim "{snippet}" is thesis-critical, and '
                    f"{ctx.score.contradicting_count} retrieved record(s) were adjudicated as "
                    "contradicting it. BioIntel flags this automatically; the cited records "
                    "should be read in full before proceeding."
                ),
                claim_id=ctx.claim_id,
                evidence_ids=_evidence_ids(ctx, "contradicting"),
                source_pages=[ctx.page_number],
            )
        )

    if claim.is_thesis_critical and ctx.score.supporting_count == 0:
        out.append(
            RuleFinding(
                rule_id="uncorroborated_thesis_claim",
                title="Thesis-critical claim has no external corroboration",
                category=RiskCategory.SCIENTIFIC,
                severity=RiskSeverity.HIGH,
                description=(
                    f'No retrieved literature supports the thesis-critical claim "{snippet}". '
                    "This is a statement about the search result, not proof the claim is false, "
                    "but it means the claim currently rests on company-internal data alone."
                ),
                claim_id=ctx.claim_id,
                source_pages=[ctx.page_number],
            )
        )

    if claim.claimed_tier is EvidenceTier.NONE_STATED and claim.importance >= 0.6:
        out.append(
            RuleFinding(
                rule_id="assertion_without_data",
                title="Material claim asserted without any stated evidence",
                category=RiskCategory.DATA_INTEGRITY,
                severity=RiskSeverity.HIGH,
                description=(
                    f'The deck asserts "{snippet}" without describing any experiment, dataset '
                    "or citation that supports it."
                ),
                claim_id=ctx.claim_id,
                source_pages=[ctx.page_number],
            )
        )

    if claim.category in _CLINICAL_CATEGORIES and claim.claimed_tier in _PRECLINICAL_TIERS:
        out.append(
            RuleFinding(
                rule_id="translational_gap",
                title="Clinical claim supported only by preclinical evidence",
                category=RiskCategory.TRANSLATIONAL,
                severity=RiskSeverity.HIGH,
                description=(
                    f'The claim "{snippet}" concerns clinical benefit or safety, but the '
                    f"strongest evidence tier the deck offers is '{claim.claimed_tier.value}'. "
                    "Cross-species and cross-tier extrapolation is the dominant failure mode "
                    "in translational biotech."
                ),
                claim_id=ctx.claim_id,
                source_pages=[ctx.page_number],
            )
        )

    if claim.has_effect_size and not claim.has_statistics:
        out.append(
            RuleFinding(
                rule_id="effect_without_statistics",
                title="Effect size reported without statistics",
                category=RiskCategory.DATA_INTEGRITY,
                severity=RiskSeverity.MEDIUM,
                description=(
                    f'The claim "{snippet}" reports a quantitative effect with no p-value, '
                    "confidence interval or variance measure. The result cannot be assessed "
                    "for significance as presented."
                ),
                claim_id=ctx.claim_id,
                source_pages=[ctx.page_number],
            )
        )

    if claim.has_effect_size and not claim.has_comparator:
        out.append(
            RuleFinding(
                rule_id="effect_without_control",
                title="Effect reported without a stated comparator",
                category=RiskCategory.DATA_INTEGRITY,
                severity=RiskSeverity.MEDIUM,
                description=(
                    f'The claim "{snippet}" reports an effect without naming the control or '
                    "comparator arm it is measured against."
                ),
                claim_id=ctx.claim_id,
                source_pages=[ctx.page_number],
            )
        )

    if claim.has_effect_size and not claim.has_sample_size:
        out.append(
            RuleFinding(
                rule_id="effect_without_n",
                title="Effect reported without sample size",
                category=RiskCategory.DATA_INTEGRITY,
                severity=RiskSeverity.LOW,
                description=(
                    f'The claim "{snippet}" does not state how many subjects, animals or '
                    "replicates the result is based on."
                ),
                claim_id=ctx.claim_id,
                source_pages=[ctx.page_number],
            )
        )

    if claim.quote_verification is QuoteVerification.FUZZY:
        out.append(
            RuleFinding(
                rule_id="approximate_provenance",
                title="Claim provenance is approximate",
                category=RiskCategory.DATA_INTEGRITY,
                severity=RiskSeverity.LOW,
                description=(
                    f'The supporting quote for "{snippet}" matched the source document only '
                    f"approximately (score {ctx.quote_match_score:.2f}). Verify the wording "
                    "against the deck before quoting it externally."
                ),
                claim_id=ctx.claim_id,
                source_pages=[ctx.page_number],
            )
        )

    if ctx.from_visual:
        out.append(
            RuleFinding(
                rule_id="claim_from_chart_reading",
                title="Claim derived from reading a chart",
                category=RiskCategory.DATA_INTEGRITY,
                severity=RiskSeverity.INFO,
                description=(
                    f'The claim "{snippet}" was recovered from a figure rather than from text. '
                    "Values read off an axis carry reading error; confirm them against the "
                    "company's source data."
                ),
                claim_id=ctx.claim_id,
                source_pages=[ctx.page_number],
            )
        )

    out.extend(_evidence_rules(ctx))
    return out


def _evidence_rules(ctx: ClaimContext) -> list[RuleFinding]:
    if ctx.adjudication is None:
        return []
    out: list[RuleFinding] = []

    retracted = [e for e in ctx.adjudication.evidence if e.record.is_retracted]
    if retracted:
        out.append(
            RuleFinding(
                rule_id="retracted_evidence",
                title="Retracted publication appeared in the evidence set",
                category=RiskCategory.DATA_INTEGRITY,
                severity=RiskSeverity.MEDIUM,
                description=(
                    "Retrieval surfaced "
                    + ", ".join(e.record.citation_label for e in retracted[:3])
                    + " which is marked as retracted. It has been excluded from scoring, but "
                    "if the company cites this work its position needs review."
                ),
                claim_id=ctx.claim_id,
                evidence_ids=[e.record.citation_label for e in retracted],
            )
        )

    stopped = [
        e
        for e in ctx.adjudication.evidence
        if (e.record.trial or {}).get("status", "").upper() in NEGATIVE_STATUSES
    ]
    if stopped:
        details = "; ".join(
            f"{e.record.nct_id} ({(e.record.trial or {}).get('status')}"
            + (
                f": {(e.record.trial or {}).get('why_stopped')}"
                if (e.record.trial or {}).get("why_stopped")
                else ""
            )
            + ")"
            for e in stopped[:3]
        )
        out.append(
            RuleFinding(
                rule_id="terminated_trial_precedent",
                title="Terminated or withdrawn trial in the same area",
                category=RiskCategory.CLINICAL,
                severity=RiskSeverity.HIGH,
                description=(
                    f"Registry search returned trials that were stopped: {details}. "
                    "Prior failures in an adjacent programme are a standard diligence question."
                ),
                claim_id=ctx.claim_id,
                evidence_ids=[e.record.citation_label for e in stopped],
            )
        )

    preprints = [e for e in ctx.adjudication.supporting if e.record.is_preprint]
    if preprints and len(preprints) == len(ctx.adjudication.supporting):
        out.append(
            RuleFinding(
                rule_id="support_only_from_preprints",
                title="Supporting evidence comes only from preprints",
                category=RiskCategory.SCIENTIFIC,
                severity=RiskSeverity.MEDIUM,
                description=(
                    "Every supporting record for this claim is a preprint and has not been "
                    "peer reviewed."
                ),
                claim_id=ctx.claim_id,
                evidence_ids=[e.record.citation_label for e in preprints],
            )
        )

    return out


# --------------------------------------------------------- portfolio rules ---
def _portfolio_rules(contexts: list[ClaimContext]) -> list[RuleFinding]:
    out: list[RuleFinding] = []
    if not contexts:
        return out

    total = len(contexts)
    no_evidence = [
        c for c in contexts if c.score.supporting_count == 0 and c.score.contradicting_count == 0
    ]
    if total >= 5 and len(no_evidence) / total >= 0.6:
        out.append(
            RuleFinding(
                rule_id="low_literature_coverage",
                title="Most claims could not be matched to external literature",
                category=RiskCategory.SCIENTIFIC,
                severity=RiskSeverity.MEDIUM,
                description=(
                    f"{len(no_evidence)} of {total} claims returned no relevant external "
                    "records. This may indicate a genuinely novel approach, or that the "
                    "claims are too company-specific to be checked externally. Either way "
                    "the analysis rests heavily on company-supplied data."
                ),
            )
        )

    hedged = [c for c in contexts if c.scoring.hedging_language and c.scoring.importance >= 0.6]
    if len(hedged) >= 3:
        out.append(
            RuleFinding(
                rule_id="pervasive_hedging",
                title="Material claims are pervasively hedged",
                category=RiskCategory.DATA_INTEGRITY,
                severity=RiskSeverity.MEDIUM,
                description=(
                    f"{len(hedged)} important claims use hedging language "
                    '("may", "potential", "designed to"). Hedging often marks the boundary '
                    "between what has been demonstrated and what is hoped for."
                ),
            )
        )

    no_clinical = all(c.scoring.claimed_tier in _PRECLINICAL_TIERS for c in contexts)
    clinical_claims = [c for c in contexts if c.scoring.category in _CLINICAL_CATEGORIES]
    if no_clinical and clinical_claims:
        out.append(
            RuleFinding(
                rule_id="no_clinical_evidence_anywhere",
                title="No clinical evidence anywhere in the deck",
                category=RiskCategory.TRANSLATIONAL,
                severity=RiskSeverity.HIGH,
                description=(
                    f"The deck makes {len(clinical_claims)} claim(s) about clinical benefit or "
                    "safety but presents no human data at any tier."
                ),
            )
        )

    return out


# ------------------------------------------------------------------ helpers ---
def _evidence_ids(ctx: ClaimContext, stance: str) -> list[str]:
    if ctx.adjudication is None:
        return []
    items = getattr(ctx.adjudication, stance, [])
    return [e.record.citation_label for e in items][:5]


def _dedupe(findings: list[RuleFinding]) -> list[RuleFinding]:
    seen: set[tuple[str, str | None]] = set()
    out: list[RuleFinding] = []
    for finding in findings:
        key = (finding.rule_id, finding.claim_id)
        if key in seen:
            continue
        seen.add(key)
        out.append(finding)
    severity_order = {
        RiskSeverity.CRITICAL: 0,
        RiskSeverity.HIGH: 1,
        RiskSeverity.MEDIUM: 2,
        RiskSeverity.LOW: 3,
        RiskSeverity.INFO: 4,
    }
    out.sort(key=lambda f: severity_order.get(f.severity, 5))
    return out


def summarise(findings: list[RuleFinding]) -> dict[str, Any]:
    by_severity: dict[str, int] = {}
    by_rule: dict[str, int] = {}
    for finding in findings:
        by_severity[finding.severity.value] = by_severity.get(finding.severity.value, 0) + 1
        by_rule[finding.rule_id] = by_rule.get(finding.rule_id, 0) + 1
    return {"total": len(findings), "by_severity": by_severity, "by_rule": by_rule}
