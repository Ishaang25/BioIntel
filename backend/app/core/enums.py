"""Domain vocabularies shared by the database, the API and the LLM schemas.

These enums are part of the product contract: they appear in JSON schemas sent
to the model, in API responses and in the database.  Changing a value is a
breaking change and needs a migration plus a schema version bump.
"""

from __future__ import annotations

from enum import StrEnum


class RunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class StageStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


class PipelineStage(StrEnum):
    """Ordered pipeline stages. Order is defined by :data:`STAGE_ORDER`."""

    PARSE = "parse"
    PAGE_UNDERSTANDING = "page_understanding"
    PROFILE = "profile"
    ENTITIES = "entities"
    CLAIMS = "claims"
    RETRIEVAL = "retrieval"
    ADJUDICATION = "adjudication"
    ASSESSMENT = "assessment"
    QUESTIONS = "questions"
    REPORT = "report"


STAGE_ORDER: tuple[PipelineStage, ...] = (
    PipelineStage.PARSE,
    PipelineStage.PAGE_UNDERSTANDING,
    PipelineStage.PROFILE,
    PipelineStage.ENTITIES,
    PipelineStage.CLAIMS,
    PipelineStage.RETRIEVAL,
    PipelineStage.ADJUDICATION,
    PipelineStage.ASSESSMENT,
    PipelineStage.QUESTIONS,
    PipelineStage.REPORT,
)

STAGE_WEIGHTS: dict[PipelineStage, float] = {
    PipelineStage.PARSE: 0.06,
    PipelineStage.PAGE_UNDERSTANDING: 0.18,
    PipelineStage.PROFILE: 0.05,
    PipelineStage.ENTITIES: 0.08,
    PipelineStage.CLAIMS: 0.15,
    PipelineStage.RETRIEVAL: 0.16,
    PipelineStage.ADJUDICATION: 0.18,
    PipelineStage.ASSESSMENT: 0.05,
    PipelineStage.QUESTIONS: 0.04,
    PipelineStage.REPORT: 0.05,
}


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class PageKind(StrEnum):
    """How a page's content was recovered."""

    DIGITAL_TEXT = "digital_text"
    SCANNED_IMAGE = "scanned_image"
    MIXED = "mixed"
    EMPTY = "empty"


class EntityType(StrEnum):
    DISEASE = "disease"
    TARGET = "target"
    DRUG = "drug"
    BIOMARKER = "biomarker"
    MECHANISM = "mechanism"
    MODALITY = "modality"
    ENDPOINT = "endpoint"
    ASSAY = "assay"
    MODEL_SYSTEM = "model_system"
    PATHWAY = "pathway"
    COMPANY = "company"
    INSTITUTION = "institution"


class ClaimCategory(StrEnum):
    MECHANISM = "mechanism"
    PRECLINICAL_EFFICACY = "preclinical_efficacy"
    CLINICAL_EFFICACY = "clinical_efficacy"
    SAFETY = "safety"
    BIOMARKER = "biomarker"
    TARGET_VALIDATION = "target_validation"
    MANUFACTURING = "manufacturing"
    PLATFORM = "platform"
    REGULATORY = "regulatory"
    IP = "ip"
    MARKET = "market"
    COMPETITIVE = "competitive"
    OTHER = "other"


class ClaimType(StrEnum):
    """What *kind of assertion* a claim is.

    Orthogonal to :class:`ClaimCategory` (which says what topic the claim is
    about).  This axis decides how the claim can be checked at all, and is the
    single most important input to scoring: a statement of regulatory fact, an
    experimental result, and a revenue projection are epistemically different
    objects and must not share a scoring model.
    """

    # --- checkable statements of fact ------------------------------------
    #: "mRESVIA is FDA approved for adults 60+" — verifiable against a regulator.
    REGULATORY_APPROVAL = "regulatory_approval"
    #: "Filed with a PDUFA date of May 30, 2025" — a submission status.
    REGULATORY_SUBMISSION = "regulatory_submission"
    #: "Phase 3 met its primary endpoint with ORR 42%" — a trial result.
    CLINICAL_RESULT = "clinical_result"
    #: "mRNA-1647 is in Phase 3" — a development-stage status.
    PIPELINE_STAGE = "pipeline_stage"
    #: "Reduced tumour volume 62% in the MPTP model" — animal/in vitro data.
    PRECLINICAL_RESULT = "preclinical_result"
    #: "NG-101 inhibits LRRK2 allosterically" — a mechanistic assertion.
    MECHANISM = "mechanism"
    #: "p-Rab10 is our target-engagement biomarker" — a biomarker assertion.
    BIOMARKER = "biomarker"
    #: "Well tolerated at 40x the efficacious dose" — a safety assertion.
    SAFETY = "safety"
    #: "Our LNP platform delivers to hepatocytes" — a capability of the platform.
    PLATFORM_CAPABILITY = "platform_capability"
    #: "Our Phase 1 success rate is 62% vs 35% industry" — a track record.
    TRACK_RECORD = "track_record"
    #: "Composition-of-matter patent granted" — an IP assertion.
    IP_POSITION = "ip_position"
    #: "Partnered with Vertex on VX-522" — a business-relationship fact.
    PARTNERSHIP = "partnership"
    #: "Cost of goods below $2/dose at scale" — a CMC assertion.
    MANUFACTURING = "manufacturing"
    #: "Only approved therapy in this setting" — a comparative assertion.
    COMPETITIVE_POSITION = "competitive_position"

    # --- statements that are not checkable against the literature ---------
    #: "$14B market opportunity" — a market estimate.
    MARKET_ESTIMATE = "market_estimate"
    #: "We expect $6B revenue in 2027" — a financial projection.
    FINANCIAL_GUIDANCE = "financial_guidance"
    #: "We will file an IND in Q3" — a plan, not a fact about the world.
    FORWARD_LOOKING = "forward_looking"
    #: "Our goal is to transform medicine" — an objective.
    STRATEGIC_OBJECTIVE = "strategic_objective"
    #: "Founded to use nature's information molecule" — corporate narrative.
    CORPORATE_VISION = "corporate_vision"
    #: "Revolutionary, best-in-class, paradigm-shifting" — unfalsifiable.
    MARKETING = "marketing"

    OTHER = "other"


class VerifiabilityClass(StrEnum):
    """How a claim can be checked, which decides what a null result means."""

    #: Checkable against a regulator or registry (FDA, EMA, ClinicalTrials.gov).
    REGISTRY_VERIFIABLE = "registry_verifiable"
    #: Checkable against the peer-reviewed literature.
    LITERATURE_VERIFIABLE = "literature_verifiable"
    #: Checkable only against company-internal data we do not have.
    COMPANY_INTERNAL = "company_internal"
    #: Not a factual claim about the present world; cannot be true or false yet.
    NOT_VERIFIABLE = "not_verifiable"


class CorroborationStatus(StrEnum):
    """The outcome of trying to check a claim.

    Replaces the old binary supported/unsupported. The distinction that
    matters most is between *nothing was found* and *something disagrees*:
    the first is a statement about our search, the second about the world.
    """

    #: Independent evidence directly confirms the claim.
    CORROBORATED = "corroborated"
    #: Independent evidence confirms part of it (e.g. the approval but not the label).
    PARTIALLY_CORROBORATED = "partially_corroborated"
    #: Nothing directly on point, but the claim sits comfortably within what
    #: the retrieved literature establishes for this target/modality/class.
    PLAUSIBLE_UNVERIFIED = "plausible_unverified"
    #: Search ran and returned nothing relevant. Says nothing about truth.
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    #: The claim's subject is verifiable in principle, but the authoritative
    #: source could not be consulted in this run (regulator API unavailable,
    #: no public record yet, press release not indexed).
    NOT_INDEPENDENTLY_VERIFIED = "not_independently_verified"
    #: Retrieved evidence genuinely disagrees with the claim.
    CONTRADICTED = "contradicted"
    #: Evidence points both ways with comparable weight.
    DISPUTED = "disputed"
    #: Not the kind of statement that can be corroborated (vision, guidance,
    #: marketing, forward-looking plans). Excluded from credibility scoring.
    NOT_ASSESSABLE = "not_assessable"


#: Which statuses are *evidence against* the company. Everything else must
#: never reduce a credibility score -- that is the Moderna bug in one line.
ADVERSE_CORROBORATION = frozenset({CorroborationStatus.CONTRADICTED, CorroborationStatus.DISPUTED})

#: Statuses that mean "we could not check", not "the claim is weak".
UNCHECKED_CORROBORATION = frozenset(
    {
        CorroborationStatus.INSUFFICIENT_EVIDENCE,
        CorroborationStatus.NOT_INDEPENDENTLY_VERIFIED,
    }
)


class ConfidenceLevel(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


def confidence_level(value: float) -> ConfidenceLevel:
    """Map a 0-1 confidence onto the three bands used throughout the memo."""
    if value >= 0.70:
        return ConfidenceLevel.HIGH
    if value >= 0.40:
        return ConfidenceLevel.MEDIUM
    return ConfidenceLevel.LOW


class EvidenceGrade(StrEnum):
    """Where a piece of evidence sits in the diligence hierarchy.

    Ordered strongest to weakest.  A regulatory approval is the strongest
    possible external corroboration of a product claim; an investor deck is
    the weakest and is not external evidence at all.
    """

    REGULATORY_APPROVAL = "regulatory_approval"
    PIVOTAL_TRIAL_TOP_JOURNAL = "pivotal_trial_top_journal"
    PIVOTAL_TRIAL = "pivotal_trial"
    META_ANALYSIS = "meta_analysis"
    SYSTEMATIC_REVIEW = "systematic_review"
    PHASE_2_TRIAL = "phase_2_trial"
    EARLY_PHASE_TRIAL = "early_phase_trial"
    REGISTRY_WITH_RESULTS = "registry_with_results"
    REGISTRY_RECORD = "registry_record"
    OBSERVATIONAL = "observational"
    PRECLINICAL = "preclinical"
    NARRATIVE_REVIEW = "narrative_review"
    PREPRINT = "preprint"
    CONFERENCE_ABSTRACT = "conference_abstract"
    COMPANY_STATEMENT = "company_statement"
    MARKETING_MATERIAL = "marketing_material"
    RETRACTED = "retracted"


#: Weight each grade carries when aggregating evidence, 0-1.
EVIDENCE_GRADE_WEIGHT: dict[EvidenceGrade, float] = {
    EvidenceGrade.REGULATORY_APPROVAL: 1.00,
    EvidenceGrade.PIVOTAL_TRIAL_TOP_JOURNAL: 0.97,
    EvidenceGrade.META_ANALYSIS: 0.94,
    EvidenceGrade.PIVOTAL_TRIAL: 0.90,
    EvidenceGrade.SYSTEMATIC_REVIEW: 0.85,
    EvidenceGrade.PHASE_2_TRIAL: 0.72,
    EvidenceGrade.REGISTRY_WITH_RESULTS: 0.62,
    EvidenceGrade.EARLY_PHASE_TRIAL: 0.58,
    EvidenceGrade.OBSERVATIONAL: 0.50,
    EvidenceGrade.REGISTRY_RECORD: 0.45,
    EvidenceGrade.PRECLINICAL: 0.38,
    EvidenceGrade.NARRATIVE_REVIEW: 0.32,
    EvidenceGrade.PREPRINT: 0.28,
    EvidenceGrade.CONFERENCE_ABSTRACT: 0.22,
    EvidenceGrade.COMPANY_STATEMENT: 0.10,
    EvidenceGrade.MARKETING_MATERIAL: 0.02,
    EvidenceGrade.RETRACTED: 0.00,
}


class VerificationStatus(StrEnum):
    """Outcome of an authoritative-source lookup (FDA, EMA, registry)."""

    VERIFIED = "verified"
    PARTIALLY_VERIFIED = "partially_verified"
    REFUTED = "refuted"
    NOT_FOUND = "not_found"
    #: The source could not be reached, or the claim type is not covered.
    NOT_ATTEMPTED = "not_attempted"
    SOURCE_UNAVAILABLE = "source_unavailable"


class ScoreDimension(StrEnum):
    """The axes of the IC scorecard.

    A single credibility number hides which risk an investor is taking.  A
    company can be scientifically excellent and commercially unready, or
    regulatorily de-risked with a thin platform.
    """

    SCIENTIFIC_VALIDITY = "scientific_validity"
    CLINICAL_MATURITY = "clinical_maturity"
    REGULATORY_CONFIDENCE = "regulatory_confidence"
    EVIDENCE_QUALITY = "evidence_quality"
    EXECUTION_CREDIBILITY = "execution_credibility"
    PLATFORM_STRENGTH = "platform_strength"
    PIPELINE_DIVERSIFICATION = "pipeline_diversification"
    TRANSLATIONAL_RISK = "translational_risk"
    COMMERCIAL_READINESS = "commercial_readiness"
    DISCLOSURE_QUALITY = "disclosure_quality"


class ICRecommendation(StrEnum):
    """The scientific-diligence recommendation, not a financial one."""

    ADVANCE = "advance"
    ADVANCE_WITH_CONDITIONS = "advance_with_conditions"
    FURTHER_DILIGENCE_REQUIRED = "further_diligence_required"
    SIGNIFICANT_CONCERNS = "significant_concerns"
    DO_NOT_ADVANCE = "do_not_advance"


class CompanyArchetype(StrEnum):
    """How the company should be assessed.

    A platform company's thesis is the platform's generalisability; a
    single-asset company's thesis is one molecule.  Scoring them the same way
    over-penalises the former for thin per-asset evidence and under-penalises
    the latter for concentration risk.
    """

    PLATFORM = "platform"
    CLINICAL_STAGE_ASSET = "clinical_stage_asset"
    PRECLINICAL_ASSET = "preclinical_asset"
    COMMERCIAL_STAGE = "commercial_stage"
    TOOLS_AND_SERVICES = "tools_and_services"
    DIAGNOSTICS = "diagnostics"
    UNKNOWN = "unknown"


class EvidenceTier(StrEnum):
    """What kind of data underpins a claim, as asserted in the deck."""

    IN_SILICO = "in_silico"
    IN_VITRO = "in_vitro"
    IN_VIVO_ANIMAL = "in_vivo_animal"
    EX_VIVO_HUMAN = "ex_vivo_human"
    CLINICAL_PHASE_1 = "clinical_phase_1"
    CLINICAL_PHASE_2 = "clinical_phase_2"
    CLINICAL_PHASE_3 = "clinical_phase_3"
    APPROVED = "approved"
    LITERATURE_ONLY = "literature_only"
    NONE_STATED = "none_stated"


#: Ordinal strength of an evidence tier, used by the scoring engine.
EVIDENCE_TIER_WEIGHT: dict[EvidenceTier, float] = {
    EvidenceTier.NONE_STATED: 0.0,
    EvidenceTier.LITERATURE_ONLY: 0.15,
    EvidenceTier.IN_SILICO: 0.20,
    EvidenceTier.IN_VITRO: 0.40,
    EvidenceTier.EX_VIVO_HUMAN: 0.55,
    EvidenceTier.IN_VIVO_ANIMAL: 0.60,
    EvidenceTier.CLINICAL_PHASE_1: 0.75,
    EvidenceTier.CLINICAL_PHASE_2: 0.88,
    EvidenceTier.CLINICAL_PHASE_3: 0.96,
    EvidenceTier.APPROVED: 1.0,
}


class AssertionBasis(StrEnum):
    """Provenance discipline: where a statement in our output came from."""

    #: Written verbatim (or near-verbatim) in the source document.
    DOCUMENT = "document"
    #: Retrieved from an external literature/registry source.
    EXTERNAL = "external"
    #: Derived by BioIntel by reasoning over document + external evidence.
    INFERRED = "inferred"


class Stance(StrEnum):
    SUPPORTS = "supports"
    CONTRADICTS = "contradicts"
    MIXED = "mixed"
    NEUTRAL = "neutral"
    UNRELATED = "unrelated"


class EvidenceSource(StrEnum):
    PUBMED = "pubmed"
    EUROPE_PMC = "europe_pmc"
    CLINICALTRIALS_GOV = "clinicaltrials_gov"
    OPENALEX = "openalex"


class PublicationType(StrEnum):
    META_ANALYSIS = "meta_analysis"
    SYSTEMATIC_REVIEW = "systematic_review"
    RANDOMIZED_TRIAL = "randomized_trial"
    CLINICAL_TRIAL = "clinical_trial"
    OBSERVATIONAL = "observational"
    PRECLINICAL = "preclinical"
    REVIEW = "review"
    PREPRINT = "preprint"
    CASE_REPORT = "case_report"
    REGISTRY_RECORD = "registry_record"
    RETRACTED = "retracted"
    OTHER = "other"


#: Study-design quality weight used when aggregating evidence.
PUBLICATION_TYPE_WEIGHT: dict[PublicationType, float] = {
    PublicationType.META_ANALYSIS: 1.00,
    PublicationType.SYSTEMATIC_REVIEW: 0.95,
    PublicationType.RANDOMIZED_TRIAL: 0.90,
    PublicationType.CLINICAL_TRIAL: 0.78,
    PublicationType.REGISTRY_RECORD: 0.55,
    PublicationType.OBSERVATIONAL: 0.55,
    PublicationType.PRECLINICAL: 0.45,
    PublicationType.REVIEW: 0.40,
    PublicationType.CASE_REPORT: 0.25,
    PublicationType.PREPRINT: 0.30,
    PublicationType.OTHER: 0.35,
    PublicationType.RETRACTED: 0.0,
}


class CredibilityBand(StrEnum):
    STRONG = "strong"
    MODERATE = "moderate"
    LIMITED = "limited"
    WEAK = "weak"
    UNSUPPORTED = "unsupported"


class RiskSeverity(StrEnum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class QuestionPriority(StrEnum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class RiskCategory(StrEnum):
    SCIENTIFIC = "scientific"
    TRANSLATIONAL = "translational"
    CLINICAL = "clinical"
    REGULATORY = "regulatory"
    MANUFACTURING = "manufacturing"
    IP = "ip"
    COMPETITIVE = "competitive"
    DATA_INTEGRITY = "data_integrity"
    TEAM = "team"
    COMMERCIAL = "commercial"


class QuoteVerification(StrEnum):
    """Result of checking a model-provided quote against the source text."""

    EXACT = "exact"
    FUZZY = "fuzzy"
    NOT_FOUND = "not_found"
    NOT_APPLICABLE = "not_applicable"
