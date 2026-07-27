"""Per-claim-type assessment policy.

The Moderna failure had a single root cause: every claim was scored by the
same formula, which asked "what experiment does the deck describe?" and gave
zero to anything that did not describe one.  An FDA approval describes no
experiment, so an approved product scored identically to a slogan.

This module makes the claim's *type* the primary control.  For each type it
declares:

* how the claim can be checked at all (:class:`VerifiabilityClass`);
* whether it belongs in a credibility score, or is excluded as unassessable;
* the prior credibility a well-formed claim of this type deserves before any
  external evidence is considered -- a regulatory approval is a checkable
  matter of public record and starts high; a mechanistic hypothesis starts
  low and must earn its score from evidence;
* what corroborating evidence would look like, so the query planner and the
  adjudicator agree on what they are searching for;
* which IC scorecard dimensions the claim informs.

Everything here is data, deliberately: it is the part of BioIntel a partner is
most likely to want to argue with, and it should be arguable without reading
control flow.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.core.enums import (
    ClaimType,
    CorroborationStatus,
    EvidenceGrade,
    ScoreDimension,
    VerifiabilityClass,
)


@dataclass(frozen=True, slots=True)
class ClaimPolicy:
    """How one kind of claim is checked, scored and reported."""

    claim_type: ClaimType
    verifiability: VerifiabilityClass

    #: Whether this claim contributes to the credibility score at all.
    #: Forward-looking statements and marketing are reported but not scored:
    #: scoring them would mean penalising a company for having a strategy.
    scorable: bool

    #: Credibility (0-1) a well-formed claim of this type carries before any
    #: external evidence. High for matters of public record that a company
    #: cannot plausibly misstate in an investor deck without legal exposure;
    #: low for hypotheses that must be earned from data.
    prior: float

    #: How much external evidence can move the score away from the prior.
    #: A regulatory fact is nearly settled by its own nature (0.35); a
    #: mechanistic claim is almost entirely decided by evidence (0.85).
    evidence_leverage: float

    #: The status assigned when no external evidence is found. This is the
    #: line that separates "we could not check" from "this looks weak".
    null_result_status: CorroborationStatus

    #: Grades of evidence that would count as genuine corroboration.
    corroborating_grades: tuple[EvidenceGrade, ...]

    #: Scorecard dimensions this claim informs, with relative weight.
    dimensions: dict[ScoreDimension, float] = field(default_factory=dict)

    #: Shown in the memo when the claim cannot be corroborated, so the reader
    #: knows why rather than seeing a bare "unsupported".
    null_result_note: str = ""

    #: True when the claim should be assessed at platform level rather than
    #: per asset (see :mod:`app.analysis.scorecard`).
    platform_level: bool = False


# Dimension shorthands, purely for readability of the table below.
_SCI = ScoreDimension.SCIENTIFIC_VALIDITY
_CLIN = ScoreDimension.CLINICAL_MATURITY
_REG = ScoreDimension.REGULATORY_CONFIDENCE
_EVQ = ScoreDimension.EVIDENCE_QUALITY
_EXE = ScoreDimension.EXECUTION_CREDIBILITY
_PLAT = ScoreDimension.PLATFORM_STRENGTH
_PIPE = ScoreDimension.PIPELINE_DIVERSIFICATION
_TRANS = ScoreDimension.TRANSLATIONAL_RISK
_COMM = ScoreDimension.COMMERCIAL_READINESS
_DISC = ScoreDimension.DISCLOSURE_QUALITY


POLICIES: dict[ClaimType, ClaimPolicy] = {
    # ================================================ regulatory facts ===
    ClaimType.REGULATORY_APPROVAL: ClaimPolicy(
        claim_type=ClaimType.REGULATORY_APPROVAL,
        verifiability=VerifiabilityClass.REGISTRY_VERIFIABLE,
        scorable=True,
        # A public, checkable matter of record that a public company cannot
        # misstate in an investor deck without securities exposure.
        prior=0.82,
        evidence_leverage=0.35,
        null_result_status=CorroborationStatus.NOT_INDEPENDENTLY_VERIFIED,
        corroborating_grades=(
            EvidenceGrade.REGULATORY_APPROVAL,
            EvidenceGrade.PIVOTAL_TRIAL_TOP_JOURNAL,
            EvidenceGrade.PIVOTAL_TRIAL,
            EvidenceGrade.REGISTRY_WITH_RESULTS,
        ),
        dimensions={_REG: 1.0, _CLIN: 0.8, _COMM: 0.7, _EXE: 0.5},
        null_result_note=(
            "Approval status is a matter of public record; absence here reflects "
            "that the regulator's database was not consulted or did not match, "
            "not doubt about the approval."
        ),
    ),
    ClaimType.REGULATORY_SUBMISSION: ClaimPolicy(
        claim_type=ClaimType.REGULATORY_SUBMISSION,
        verifiability=VerifiabilityClass.REGISTRY_VERIFIABLE,
        scorable=True,
        # Filings and PDUFA dates are usually disclosed only by the company
        # until action is taken, so they are credible but rarely confirmable.
        prior=0.68,
        evidence_leverage=0.30,
        null_result_status=CorroborationStatus.NOT_INDEPENDENTLY_VERIFIED,
        corroborating_grades=(
            EvidenceGrade.REGULATORY_APPROVAL,
            EvidenceGrade.REGISTRY_RECORD,
            EvidenceGrade.REGISTRY_WITH_RESULTS,
        ),
        dimensions={_REG: 1.0, _EXE: 0.6, _COMM: 0.4},
        null_result_note=(
            "Regulators do not publish pending submissions; this is normally "
            "confirmable only from company correspondence, so absence of an "
            "external record is expected rather than informative."
        ),
    ),
    # ================================================== clinical facts ===
    ClaimType.CLINICAL_RESULT: ClaimPolicy(
        claim_type=ClaimType.CLINICAL_RESULT,
        verifiability=VerifiabilityClass.LITERATURE_VERIFIABLE,
        scorable=True,
        prior=0.45,
        evidence_leverage=0.80,
        null_result_status=CorroborationStatus.INSUFFICIENT_EVIDENCE,
        corroborating_grades=(
            EvidenceGrade.PIVOTAL_TRIAL_TOP_JOURNAL,
            EvidenceGrade.PIVOTAL_TRIAL,
            EvidenceGrade.META_ANALYSIS,
            EvidenceGrade.PHASE_2_TRIAL,
            EvidenceGrade.EARLY_PHASE_TRIAL,
            EvidenceGrade.REGISTRY_WITH_RESULTS,
        ),
        dimensions={_CLIN: 1.0, _SCI: 0.8, _EVQ: 0.9, _TRANS: 0.7, _REG: 0.4},
        null_result_note=(
            "Trial results are often presented at conferences or filed before "
            "peer-reviewed publication; request the clinical study report."
        ),
    ),
    ClaimType.PIPELINE_STAGE: ClaimPolicy(
        claim_type=ClaimType.PIPELINE_STAGE,
        verifiability=VerifiabilityClass.REGISTRY_VERIFIABLE,
        scorable=True,
        # Trial registration is mandatory for interventional studies, so this
        # is genuinely checkable and a mismatch is meaningful.
        prior=0.70,
        evidence_leverage=0.55,
        null_result_status=CorroborationStatus.NOT_INDEPENDENTLY_VERIFIED,
        corroborating_grades=(
            EvidenceGrade.REGISTRY_RECORD,
            EvidenceGrade.REGISTRY_WITH_RESULTS,
            EvidenceGrade.PIVOTAL_TRIAL,
            EvidenceGrade.PHASE_2_TRIAL,
            EvidenceGrade.EARLY_PHASE_TRIAL,
        ),
        dimensions={_CLIN: 0.9, _PIPE: 1.0, _EXE: 0.6, _REG: 0.3},
        null_result_note=(
            "Interventional trials must be registered; if no registry record "
            "matched, confirm the trial identifier with the company."
        ),
    ),
    ClaimType.PRECLINICAL_RESULT: ClaimPolicy(
        claim_type=ClaimType.PRECLINICAL_RESULT,
        verifiability=VerifiabilityClass.COMPANY_INTERNAL,
        scorable=True,
        # Almost always unpublished proprietary data at the point of a raise.
        prior=0.38,
        evidence_leverage=0.65,
        null_result_status=CorroborationStatus.INSUFFICIENT_EVIDENCE,
        corroborating_grades=(EvidenceGrade.PRECLINICAL, EvidenceGrade.NARRATIVE_REVIEW),
        dimensions={_SCI: 0.9, _TRANS: 1.0, _EVQ: 0.7},
        null_result_note=(
            "Preclinical data supporting a raise is usually unpublished; the "
            "absence of an external match is the normal case, not a red flag."
        ),
    ),
    ClaimType.MECHANISM: ClaimPolicy(
        claim_type=ClaimType.MECHANISM,
        verifiability=VerifiabilityClass.LITERATURE_VERIFIABLE,
        scorable=True,
        # Mechanism is the one thing the literature can genuinely adjudicate.
        prior=0.35,
        evidence_leverage=0.85,
        null_result_status=CorroborationStatus.INSUFFICIENT_EVIDENCE,
        corroborating_grades=(
            EvidenceGrade.META_ANALYSIS,
            EvidenceGrade.SYSTEMATIC_REVIEW,
            EvidenceGrade.PIVOTAL_TRIAL,
            EvidenceGrade.PHASE_2_TRIAL,
            EvidenceGrade.PRECLINICAL,
            EvidenceGrade.NARRATIVE_REVIEW,
        ),
        dimensions={_SCI: 1.0, _TRANS: 0.8, _EVQ: 0.6, _PLAT: 0.4},
        null_result_note=(
            "A mechanism with no literature footprint is either genuinely novel "
            "or has been tried and abandoned; distinguishing the two is a "
            "priority diligence question."
        ),
    ),
    ClaimType.BIOMARKER: ClaimPolicy(
        claim_type=ClaimType.BIOMARKER,
        verifiability=VerifiabilityClass.LITERATURE_VERIFIABLE,
        scorable=True,
        prior=0.40,
        evidence_leverage=0.75,
        null_result_status=CorroborationStatus.INSUFFICIENT_EVIDENCE,
        corroborating_grades=(
            EvidenceGrade.META_ANALYSIS,
            EvidenceGrade.PIVOTAL_TRIAL,
            EvidenceGrade.PHASE_2_TRIAL,
            EvidenceGrade.OBSERVATIONAL,
            EvidenceGrade.PRECLINICAL,
        ),
        dimensions={_SCI: 0.7, _CLIN: 0.6, _TRANS: 0.9, _REG: 0.5},
        null_result_note=(
            "Biomarker qualification is a regulatory process; an unqualified "
            "biomarker cannot by itself support an approval pathway."
        ),
    ),
    ClaimType.SAFETY: ClaimPolicy(
        claim_type=ClaimType.SAFETY,
        verifiability=VerifiabilityClass.COMPANY_INTERNAL,
        scorable=True,
        prior=0.42,
        evidence_leverage=0.75,
        null_result_status=CorroborationStatus.INSUFFICIENT_EVIDENCE,
        corroborating_grades=(
            EvidenceGrade.PIVOTAL_TRIAL_TOP_JOURNAL,
            EvidenceGrade.PIVOTAL_TRIAL,
            EvidenceGrade.META_ANALYSIS,
            EvidenceGrade.PHASE_2_TRIAL,
            EvidenceGrade.EARLY_PHASE_TRIAL,
            EvidenceGrade.PRECLINICAL,
        ),
        dimensions={_CLIN: 0.8, _SCI: 0.5, _TRANS: 0.9, _REG: 0.7},
        null_result_note=(
            "Safety claims should be checked against class-wide liabilities as "
            "well as the company's own dataset."
        ),
    ),
    # ================================================ platform & track ===
    ClaimType.PLATFORM_CAPABILITY: ClaimPolicy(
        claim_type=ClaimType.PLATFORM_CAPABILITY,
        verifiability=VerifiabilityClass.LITERATURE_VERIFIABLE,
        scorable=True,
        # Platform claims are corroborated in aggregate across assets rather
        # than one at a time; see the platform coherence logic in scorecard.
        prior=0.42,
        evidence_leverage=0.70,
        null_result_status=CorroborationStatus.PLAUSIBLE_UNVERIFIED,
        corroborating_grades=(
            EvidenceGrade.REGULATORY_APPROVAL,
            EvidenceGrade.PIVOTAL_TRIAL,
            EvidenceGrade.PHASE_2_TRIAL,
            EvidenceGrade.EARLY_PHASE_TRIAL,
            EvidenceGrade.PRECLINICAL,
            EvidenceGrade.NARRATIVE_REVIEW,
        ),
        dimensions={_PLAT: 1.0, _SCI: 0.7, _PIPE: 0.5, _TRANS: 0.6},
        platform_level=True,
        null_result_note=(
            "Platform capability is evidenced by the portfolio as a whole; a "
            "single unmatched statement is weak evidence either way."
        ),
    ),
    ClaimType.TRACK_RECORD: ClaimPolicy(
        claim_type=ClaimType.TRACK_RECORD,
        verifiability=VerifiabilityClass.COMPANY_INTERNAL,
        scorable=True,
        # Self-reported benchmarks with company-chosen denominators. Credible
        # as a directional signal, never as an audited statistic.
        prior=0.35,
        evidence_leverage=0.40,
        null_result_status=CorroborationStatus.NOT_INDEPENDENTLY_VERIFIED,
        corroborating_grades=(
            EvidenceGrade.META_ANALYSIS,
            EvidenceGrade.SYSTEMATIC_REVIEW,
            EvidenceGrade.OBSERVATIONAL,
        ),
        dimensions={_EXE: 1.0, _PLAT: 0.6, _DISC: 0.8},
        null_result_note=(
            "Self-reported success rates depend entirely on the denominator and "
            "time window chosen; request the underlying program list."
        ),
    ),
    # ================================================== business facts ===
    ClaimType.IP_POSITION: ClaimPolicy(
        claim_type=ClaimType.IP_POSITION,
        verifiability=VerifiabilityClass.REGISTRY_VERIFIABLE,
        scorable=True,
        prior=0.60,
        evidence_leverage=0.30,
        null_result_status=CorroborationStatus.NOT_INDEPENDENTLY_VERIFIED,
        corroborating_grades=(EvidenceGrade.REGISTRY_RECORD,),
        dimensions={_COMM: 0.6, _EXE: 0.4},
        null_result_note=(
            "Patent status requires a patent-office search, which BioIntel does "
            "not perform; treat as unverified rather than unsupported."
        ),
    ),
    ClaimType.PARTNERSHIP: ClaimPolicy(
        claim_type=ClaimType.PARTNERSHIP,
        verifiability=VerifiabilityClass.REGISTRY_VERIFIABLE,
        scorable=True,
        # A named partner is a strong signal: it means a counterparty with
        # its own diligence process reached a positive conclusion.
        prior=0.70,
        evidence_leverage=0.45,
        null_result_status=CorroborationStatus.NOT_INDEPENDENTLY_VERIFIED,
        corroborating_grades=(
            EvidenceGrade.REGISTRY_RECORD,
            EvidenceGrade.REGISTRY_WITH_RESULTS,
            EvidenceGrade.PIVOTAL_TRIAL,
            EvidenceGrade.PHASE_2_TRIAL,
        ),
        dimensions={_EXE: 0.9, _COMM: 0.7, _SCI: 0.3, _PLAT: 0.5},
        null_result_note=(
            "Collaborations appear in trial registrations as co-sponsors; check "
            "the sponsor field of the relevant studies."
        ),
    ),
    ClaimType.MANUFACTURING: ClaimPolicy(
        claim_type=ClaimType.MANUFACTURING,
        verifiability=VerifiabilityClass.COMPANY_INTERNAL,
        scorable=True,
        prior=0.45,
        evidence_leverage=0.40,
        null_result_status=CorroborationStatus.INSUFFICIENT_EVIDENCE,
        corroborating_grades=(EvidenceGrade.NARRATIVE_REVIEW, EvidenceGrade.PRECLINICAL),
        dimensions={_COMM: 0.9, _EXE: 0.7, _TRANS: 0.4},
        null_result_note="CMC detail is rarely published; expect to diligence this directly.",
    ),
    ClaimType.COMPETITIVE_POSITION: ClaimPolicy(
        claim_type=ClaimType.COMPETITIVE_POSITION,
        verifiability=VerifiabilityClass.LITERATURE_VERIFIABLE,
        scorable=True,
        # Companies systematically flatter themselves here, and unlike the
        # other categories the literature can actually check it.
        prior=0.32,
        evidence_leverage=0.80,
        null_result_status=CorroborationStatus.INSUFFICIENT_EVIDENCE,
        corroborating_grades=(
            EvidenceGrade.REGULATORY_APPROVAL,
            EvidenceGrade.PIVOTAL_TRIAL,
            EvidenceGrade.META_ANALYSIS,
            EvidenceGrade.REGISTRY_RECORD,
            EvidenceGrade.SYSTEMATIC_REVIEW,
        ),
        dimensions={_COMM: 0.8, _SCI: 0.4, _EVQ: 0.5},
        null_result_note=(
            "'First-in-class' and 'best-in-class' claims are checkable against "
            "the trial registry; a null result may mean the search missed "
            "competitors rather than that none exist."
        ),
    ),
    # ============================================ not credibility-bearing ===
    ClaimType.MARKET_ESTIMATE: ClaimPolicy(
        claim_type=ClaimType.MARKET_ESTIMATE,
        verifiability=VerifiabilityClass.NOT_VERIFIABLE,
        scorable=False,
        prior=0.0,
        evidence_leverage=0.0,
        null_result_status=CorroborationStatus.NOT_ASSESSABLE,
        corroborating_grades=(),
        dimensions={_COMM: 0.2},
        null_result_note=(
            "Market sizing is an analyst input, not a scientific claim; it is "
            "reported for the commercial team but excluded from credibility."
        ),
    ),
    ClaimType.FINANCIAL_GUIDANCE: ClaimPolicy(
        claim_type=ClaimType.FINANCIAL_GUIDANCE,
        verifiability=VerifiabilityClass.NOT_VERIFIABLE,
        scorable=False,
        prior=0.0,
        evidence_leverage=0.0,
        null_result_status=CorroborationStatus.NOT_ASSESSABLE,
        corroborating_grades=(),
        dimensions={},
        null_result_note=(
            "Financial guidance is a projection about the future and cannot be "
            "true or false today; excluded from scientific credibility."
        ),
    ),
    ClaimType.FORWARD_LOOKING: ClaimPolicy(
        claim_type=ClaimType.FORWARD_LOOKING,
        verifiability=VerifiabilityClass.NOT_VERIFIABLE,
        scorable=False,
        prior=0.0,
        evidence_leverage=0.0,
        null_result_status=CorroborationStatus.NOT_ASSESSABLE,
        corroborating_grades=(),
        dimensions={_EXE: 0.2},
        null_result_note=(
            "A stated plan is a commitment, not a claim about the world. It is "
            "tracked as an execution milestone, not scored for credibility."
        ),
    ),
    ClaimType.STRATEGIC_OBJECTIVE: ClaimPolicy(
        claim_type=ClaimType.STRATEGIC_OBJECTIVE,
        verifiability=VerifiabilityClass.NOT_VERIFIABLE,
        scorable=False,
        prior=0.0,
        evidence_leverage=0.0,
        null_result_status=CorroborationStatus.NOT_ASSESSABLE,
        corroborating_grades=(),
        dimensions={},
        null_result_note="An objective cannot be corroborated; excluded from scoring.",
    ),
    ClaimType.CORPORATE_VISION: ClaimPolicy(
        claim_type=ClaimType.CORPORATE_VISION,
        verifiability=VerifiabilityClass.NOT_VERIFIABLE,
        scorable=False,
        prior=0.0,
        evidence_leverage=0.0,
        null_result_status=CorroborationStatus.NOT_ASSESSABLE,
        corroborating_grades=(),
        dimensions={},
        null_result_note="Corporate narrative; excluded from scoring.",
    ),
    ClaimType.MARKETING: ClaimPolicy(
        claim_type=ClaimType.MARKETING,
        verifiability=VerifiabilityClass.NOT_VERIFIABLE,
        scorable=False,
        prior=0.0,
        evidence_leverage=0.0,
        null_result_status=CorroborationStatus.NOT_ASSESSABLE,
        corroborating_grades=(),
        # Counted only as a disclosure-quality signal: a deck that is mostly
        # superlatives tells you something, just not about the science.
        dimensions={_DISC: 0.5},
        null_result_note=(
            "Unfalsifiable promotional language; excluded from credibility but "
            "counted against disclosure quality."
        ),
    ),
    ClaimType.OTHER: ClaimPolicy(
        claim_type=ClaimType.OTHER,
        verifiability=VerifiabilityClass.LITERATURE_VERIFIABLE,
        scorable=True,
        prior=0.40,
        evidence_leverage=0.60,
        null_result_status=CorroborationStatus.INSUFFICIENT_EVIDENCE,
        corroborating_grades=(
            EvidenceGrade.PIVOTAL_TRIAL,
            EvidenceGrade.PHASE_2_TRIAL,
            EvidenceGrade.REGISTRY_RECORD,
            EvidenceGrade.NARRATIVE_REVIEW,
        ),
        dimensions={_SCI: 0.4, _EVQ: 0.3},
        null_result_note="",
    ),
}


#: Fallback for a type the model somehow returns that we do not know.
DEFAULT_POLICY = POLICIES[ClaimType.OTHER]


def policy_for(claim_type: ClaimType | str) -> ClaimPolicy:
    """Return the policy for ``claim_type``, never raising."""
    if isinstance(claim_type, str):
        try:
            claim_type = ClaimType(claim_type)
        except ValueError:
            return DEFAULT_POLICY
    return POLICIES.get(claim_type, DEFAULT_POLICY)


def is_scorable(claim_type: ClaimType | str) -> bool:
    return policy_for(claim_type).scorable


def null_status_for(claim_type: ClaimType | str) -> CorroborationStatus:
    """What it means that nothing was found for a claim of this type."""
    return policy_for(claim_type).null_result_status


#: Claim types whose subject a regulator or registry can settle directly.
REGISTRY_VERIFIABLE_TYPES = frozenset(
    ct
    for ct, policy in POLICIES.items()
    if policy.verifiability is VerifiabilityClass.REGISTRY_VERIFIABLE
)

#: Claim types excluded from credibility scoring entirely.
UNSCORABLE_TYPES = frozenset(ct for ct, policy in POLICIES.items() if not policy.scorable)

#: Claim types assessed at platform rather than asset level.
PLATFORM_LEVEL_TYPES = frozenset(ct for ct, policy in POLICIES.items() if policy.platform_level)


def corroboration_guidance(claim_type: ClaimType | str) -> str:
    """Prose telling the model what would actually corroborate this claim.

    Injected into the adjudication and verdict prompts so the adjudicator is
    searching for the right thing.  Without it, a model asked to judge an
    approval claim against PubMed abstracts will grade topical proximity and
    call the result "unsupported" -- the failure this system was corrected for.
    """
    policy = policy_for(claim_type)
    grades = ", ".join(g.value.replace("_", " ") for g in policy.corroborating_grades)

    verifiability = {
        VerifiabilityClass.REGISTRY_VERIFIABLE: (
            "This claim is settled by a regulator or a trial registry, not by the "
            "literature. Peer-reviewed papers can only ever be indirect evidence for it."
        ),
        VerifiabilityClass.LITERATURE_VERIFIABLE: (
            "This claim is the kind the peer-reviewed literature can genuinely adjudicate."
        ),
        VerifiabilityClass.COMPANY_INTERNAL: (
            "This claim rests on data the company holds and has probably not published. "
            "External silence is the expected default, not a warning sign."
        ),
        VerifiabilityClass.NOT_VERIFIABLE: (
            "This is not a factual claim about the present world and cannot be corroborated."
        ),
    }[policy.verifiability]

    lines = [verifiability]
    if grades:
        lines.append(f"Evidence capable of corroborating it: {grades}.")
    if policy.null_result_note:
        lines.append(f"If nothing is found: {policy.null_result_note}")
    lines.append(
        f"A null result for this claim type is recorded as "
        f"'{policy.null_result_status.value.replace('_', ' ')}', which is not a finding "
        "against the company."
    )
    return " ".join(lines)
