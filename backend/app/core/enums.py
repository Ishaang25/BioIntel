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
