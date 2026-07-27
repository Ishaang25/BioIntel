"""Evidence hierarchy grading.

Evidence is not equal, and the previous scoring treated it as nearly so.  A
Phase 3 result in the New England Journal and a narrative review in a
low-profile journal both entered the score as "a paper".

This module assigns each retrieved record a place in an explicit diligence
hierarchy:

    regulatory approval
      > pivotal trial in a top-tier journal
      > meta-analysis
      > pivotal trial
      > systematic review
      > Phase 2 trial
      > registry record with posted results
      > early-phase trial
      > observational study
      > registry record without results
      > preclinical
      > narrative review
      > preprint
      > conference abstract
      > company statement
      > marketing material

Grading is deterministic and derived from record metadata (publication type,
journal, registry phase, results-posted flag) so it can be audited, and so
two runs over the same corpus grade it identically.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.core.enums import (
    EVIDENCE_GRADE_WEIGHT,
    EvidenceGrade,
    EvidenceSource,
    PublicationType,
)
from app.evidence.models import EvidenceRecord

#: Journals whose peer review and readership make a result field-defining.
#: Used only to promote an already-pivotal trial one grade; it never rescues
#: a weak design, because venue is not a substitute for method.
TOP_TIER_JOURNALS: frozenset[str] = frozenset(
    {
        "new england journal of medicine",
        "n engl j med",
        "the lancet",
        "lancet",
        "jama",
        "journal of the american medical association",
        "nature",
        "science",
        "cell",
        "nature medicine",
        "nat med",
        "the bmj",
        "bmj",
        "annals of internal medicine",
        "circulation",
        "journal of clinical oncology",
        "j clin oncol",
        "lancet oncology",
        "lancet respiratory medicine",
        "lancet neurology",
        "blood",
        "european heart journal",
        "gastroenterology",
        "immunity",
        "cancer cell",
        "nature biotechnology",
        "nat biotechnol",
    }
)

#: Second tier: strong specialist venues. Not promoted, but not discounted.
STRONG_JOURNALS: frozenset[str] = frozenset(
    {
        "science translational medicine",
        "nature communications",
        "nat commun",
        "journal of infectious diseases",
        "clinical infectious diseases",
        "vaccine",
        "diabetes care",
        "kidney international",
        "american journal of respiratory and critical care medicine",
        "molecular therapy",
        "journal of hepatology",
        "annals of oncology",
        "neurology",
        "brain",
        "arthritis and rheumatology",
        "journal of allergy and clinical immunology",
    }
)

_PREPRINT_SERVERS = ("biorxiv", "medrxiv", "arxiv", "research square", "ssrn", "preprints.org")

_PHASE_3_MARKERS = ("phase 3", "phase iii", "phase3", "PHASE3")
_PHASE_2_MARKERS = ("phase 2", "phase ii", "phase2", "PHASE2")
_PHASE_1_MARKERS = ("phase 1", "phase i ", "phase1", "PHASE1", "first-in-human")

_ABSTRACT_MARKERS = ("conference abstract", "meeting abstract", "poster", "oral presentation")

_PIVOTAL_TITLE_RE = re.compile(
    r"\b(phase\s*(?:3|iii)|pivotal|registrational|confirmatory)\b", re.IGNORECASE
)
_PHASE2_TITLE_RE = re.compile(r"\bphase\s*(?:2|ii|1/2|i/ii|2b|iib)\b", re.IGNORECASE)
_PHASE1_TITLE_RE = re.compile(r"\bphase\s*(?:1|i)\b(?!\s*/)", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class GradedEvidence:
    """A record placed in the hierarchy, with the reason it landed there."""

    grade: EvidenceGrade
    weight: float
    rationale: str
    #: True when the record can settle a regulatory question by itself.
    is_authoritative: bool = False


def normalise_journal(journal: str | None) -> str:
    if not journal:
        return ""
    cleaned = journal.strip().lower().rstrip(".")
    cleaned = re.sub(r"^the\s+", "", cleaned)
    return re.sub(r"\s+", " ", cleaned)


def journal_tier(journal: str | None) -> int:
    """0 = top tier, 1 = strong specialist, 2 = other/unknown."""
    name = normalise_journal(journal)
    if not name:
        return 2
    if name in TOP_TIER_JOURNALS:
        return 0
    if name in STRONG_JOURNALS:
        return 1
    # Some sources return the ISO abbreviation with punctuation stripped.
    compact = name.replace(".", "").replace(" ", "")
    for candidate in TOP_TIER_JOURNALS:
        if compact == candidate.replace(".", "").replace(" ", ""):
            return 0
    return 2


def _trial_phase(record: EvidenceRecord) -> str | None:
    """Highest phase mentioned in the record's registry metadata or types."""
    haystack = " ".join(
        [
            *(record.trial.get("phases") or []),
            *record.publication_types,
            record.title,
        ]
    ).lower()
    if any(marker in haystack for marker in _PHASE_3_MARKERS):
        return "3"
    if any(marker in haystack for marker in _PHASE_2_MARKERS):
        return "2"
    if any(marker in haystack for marker in _PHASE_1_MARKERS):
        return "1"
    return None


def grade_record(record: EvidenceRecord) -> GradedEvidence:
    """Place ``record`` in the evidence hierarchy."""
    if record.is_retracted:
        return GradedEvidence(
            EvidenceGrade.RETRACTED,
            EVIDENCE_GRADE_WEIGHT[EvidenceGrade.RETRACTED],
            "The publication has been retracted and carries no evidential weight.",
        )

    # ---- registry records ------------------------------------------------
    if record.source is EvidenceSource.CLINICALTRIALS_GOV:
        return _grade_registry(record)

    lowered_types = " ".join(record.publication_types).lower()

    if any(marker in lowered_types for marker in _ABSTRACT_MARKERS):
        return GradedEvidence(
            EvidenceGrade.CONFERENCE_ABSTRACT,
            EVIDENCE_GRADE_WEIGHT[EvidenceGrade.CONFERENCE_ABSTRACT],
            "Conference abstract: not peer reviewed in full and often superseded.",
        )

    if record.is_preprint or any(
        server in normalise_journal(record.journal) for server in _PREPRINT_SERVERS
    ):
        return GradedEvidence(
            EvidenceGrade.PREPRINT,
            EVIDENCE_GRADE_WEIGHT[EvidenceGrade.PREPRINT],
            "Preprint: not peer reviewed.",
        )

    design = record.study_design
    tier = journal_tier(record.journal)
    venue = _venue_phrase(record.journal, tier)

    if design is PublicationType.META_ANALYSIS:
        return GradedEvidence(
            EvidenceGrade.META_ANALYSIS,
            EVIDENCE_GRADE_WEIGHT[EvidenceGrade.META_ANALYSIS],
            f"Meta-analysis{venue}: pools multiple trials and sits near the top of the hierarchy.",
        )

    if design is PublicationType.SYSTEMATIC_REVIEW:
        return GradedEvidence(
            EvidenceGrade.SYSTEMATIC_REVIEW,
            EVIDENCE_GRADE_WEIGHT[EvidenceGrade.SYSTEMATIC_REVIEW],
            f"Systematic review{venue}: synthesises primary studies without pooling effect sizes.",
        )

    if design in (PublicationType.RANDOMIZED_TRIAL, PublicationType.CLINICAL_TRIAL):
        return _grade_trial_publication(record, tier, venue)

    if design is PublicationType.OBSERVATIONAL:
        return GradedEvidence(
            EvidenceGrade.OBSERVATIONAL,
            EVIDENCE_GRADE_WEIGHT[EvidenceGrade.OBSERVATIONAL],
            f"Observational study{venue}: informative about association, not causation.",
        )

    if design is PublicationType.PRECLINICAL:
        return GradedEvidence(
            EvidenceGrade.PRECLINICAL,
            EVIDENCE_GRADE_WEIGHT[EvidenceGrade.PRECLINICAL],
            f"Preclinical study{venue}: animal or in vitro evidence only.",
        )

    if design is PublicationType.REVIEW:
        return GradedEvidence(
            EvidenceGrade.NARRATIVE_REVIEW,
            EVIDENCE_GRADE_WEIGHT[EvidenceGrade.NARRATIVE_REVIEW],
            f"Narrative review{venue}: restates others' findings; secondary evidence.",
        )

    if design is PublicationType.CASE_REPORT:
        return GradedEvidence(
            EvidenceGrade.OBSERVATIONAL,
            EVIDENCE_GRADE_WEIGHT[EvidenceGrade.OBSERVATIONAL] * 0.5,
            f"Case report{venue}: single-patient evidence; hypothesis-generating only.",
        )

    # Unclassified primary literature: infer from the title where possible.
    if _PIVOTAL_TITLE_RE.search(record.title):
        return _grade_trial_publication(record, tier, venue)
    if _PHASE2_TITLE_RE.search(record.title):
        return GradedEvidence(
            EvidenceGrade.PHASE_2_TRIAL,
            EVIDENCE_GRADE_WEIGHT[EvidenceGrade.PHASE_2_TRIAL],
            f"Phase 2 study{venue} inferred from the title.",
        )

    return GradedEvidence(
        EvidenceGrade.NARRATIVE_REVIEW,
        EVIDENCE_GRADE_WEIGHT[EvidenceGrade.NARRATIVE_REVIEW] * 0.9,
        f"Unclassified publication{venue}: graded conservatively.",
    )


def _venue_phrase(journal: str | None, tier: int) -> str:
    if not journal:
        return ""
    if tier == 0:
        return f" in a top-tier journal ({journal})"
    if tier == 1:
        return f" in a strong specialist journal ({journal})"
    return f" in {journal}"


def _grade_trial_publication(record: EvidenceRecord, tier: int, venue: str) -> GradedEvidence:
    phase = _trial_phase(record)

    if phase == "3":
        if tier == 0:
            return GradedEvidence(
                EvidenceGrade.PIVOTAL_TRIAL_TOP_JOURNAL,
                EVIDENCE_GRADE_WEIGHT[EvidenceGrade.PIVOTAL_TRIAL_TOP_JOURNAL],
                f"Pivotal (Phase 3) trial published{venue}: the strongest clinical evidence short "
                "of an approval.",
            )
        return GradedEvidence(
            EvidenceGrade.PIVOTAL_TRIAL,
            EVIDENCE_GRADE_WEIGHT[EvidenceGrade.PIVOTAL_TRIAL],
            f"Pivotal (Phase 3) trial{venue}.",
        )

    if phase == "2":
        return GradedEvidence(
            EvidenceGrade.PHASE_2_TRIAL,
            EVIDENCE_GRADE_WEIGHT[EvidenceGrade.PHASE_2_TRIAL],
            f"Phase 2 trial{venue}: establishes signal, not registrational proof.",
        )

    if phase == "1":
        return GradedEvidence(
            EvidenceGrade.EARLY_PHASE_TRIAL,
            EVIDENCE_GRADE_WEIGHT[EvidenceGrade.EARLY_PHASE_TRIAL],
            f"Early-phase (Phase 1) trial{venue}: safety and PK, not efficacy.",
        )

    # A randomised trial of unstated phase still beats an uncontrolled one.
    if record.study_design is PublicationType.RANDOMIZED_TRIAL:
        return GradedEvidence(
            EvidenceGrade.PHASE_2_TRIAL,
            EVIDENCE_GRADE_WEIGHT[EvidenceGrade.PHASE_2_TRIAL] * 0.9,
            f"Randomised controlled trial of unstated phase{venue}.",
        )
    return GradedEvidence(
        EvidenceGrade.EARLY_PHASE_TRIAL,
        EVIDENCE_GRADE_WEIGHT[EvidenceGrade.EARLY_PHASE_TRIAL],
        f"Clinical trial of unstated phase{venue}.",
    )


def _grade_registry(record: EvidenceRecord) -> GradedEvidence:
    trial = record.trial or {}
    phase = _trial_phase(record)
    has_results = bool(trial.get("has_results"))
    status = str(trial.get("status", "")).upper()

    if has_results:
        phase_label = f"Phase {phase} " if phase else ""
        return GradedEvidence(
            EvidenceGrade.REGISTRY_WITH_RESULTS,
            EVIDENCE_GRADE_WEIGHT[EvidenceGrade.REGISTRY_WITH_RESULTS],
            f"{phase_label}registry record with posted results: outcomes are on the public record.",
            is_authoritative=True,
        )

    detail = f" (status: {status.title()})" if status else ""
    return GradedEvidence(
        EvidenceGrade.REGISTRY_RECORD,
        EVIDENCE_GRADE_WEIGHT[EvidenceGrade.REGISTRY_RECORD],
        f"Registry record without posted results{detail}: establishes that the trial exists and "
        "its design, not that it worked.",
        #: Authoritative for existence and stage questions, not for efficacy.
        is_authoritative=True,
    )


def grade_weight(record: EvidenceRecord) -> float:
    """Convenience: the 0-1 hierarchy weight for ``record``."""
    return grade_record(record).weight


def best_grade(records: list[EvidenceRecord]) -> EvidenceGrade | None:
    """The strongest grade present in ``records``."""
    if not records:
        return None
    return max(
        (grade_record(r).grade for r in records),
        key=lambda g: EVIDENCE_GRADE_WEIGHT.get(g, 0.0),
    )


def grade_distribution(records: list[EvidenceRecord]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for record in records:
        key = grade_record(record).grade.value
        counts[key] = counts.get(key, 0) + 1
    return counts
