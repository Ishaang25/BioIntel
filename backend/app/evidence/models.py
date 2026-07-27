"""Normalised evidence record.

Every external source is mapped onto this one shape so that ranking, scoring
and adjudication never need to know where a record came from.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any

from app.core.enums import EvidenceSource, PublicationType

#: Mapping from PubMed publication-type strings to our design taxonomy,
#: checked in order (first match wins, most specific first).
_DESIGN_RULES: tuple[tuple[PublicationType, tuple[str, ...]], ...] = (
    (PublicationType.RETRACTED, ("retracted publication", "retraction of publication")),
    (PublicationType.META_ANALYSIS, ("meta-analysis",)),
    (PublicationType.SYSTEMATIC_REVIEW, ("systematic review",)),
    (
        PublicationType.RANDOMIZED_TRIAL,
        ("randomized controlled trial", "controlled clinical trial"),
    ),
    (
        PublicationType.CLINICAL_TRIAL,
        (
            "clinical trial",
            "clinical trial, phase i",
            "clinical trial, phase ii",
            "clinical trial, phase iii",
            "clinical trial, phase iv",
            "pragmatic clinical trial",
        ),
    ),
    (PublicationType.CASE_REPORT, ("case reports",)),
    (
        PublicationType.OBSERVATIONAL,
        (
            "observational study",
            "comparative study",
            "multicenter study",
            "cohort studies",
            "twin study",
        ),
    ),
    (PublicationType.REVIEW, ("review", "scoping review", "narrative review")),
    (PublicationType.PREPRINT, ("preprint",)),
)

#: Abstract cues used when publication types are absent or uninformative.
_PRECLINICAL_CUES = (
    "mice",
    "mouse",
    "murine",
    "rat ",
    "rats",
    "in vitro",
    "cell line",
    "xenograft",
    "zebrafish",
    "drosophila",
    "c. elegans",
    "knockout",
    "transfected",
    "cultured cells",
)
_CLINICAL_CUES = ("patients", "participants", "enrolled", "randomized", "randomised", "placebo")


@dataclass(slots=True)
class EvidenceRecord:
    """A single external record, source-agnostic."""

    source: EvidenceSource
    external_id: str
    title: str = ""
    abstract: str = ""
    journal: str | None = None
    publication_year: int | None = None
    publication_date: str | None = None
    authors: list[str] = field(default_factory=list)
    publication_types: list[str] = field(default_factory=list)
    mesh_terms: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    doi: str | None = None
    pmid: str | None = None
    pmcid: str | None = None
    nct_id: str | None = None
    url: str | None = None
    citation_count: int | None = None
    is_retracted: bool = False
    is_preprint: bool = False
    trial: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)
    #: The query that surfaced this record (kept for provenance).
    retrieval_query: str | None = None

    # ------------------------------------------------------------ derived ---
    @property
    def study_design(self) -> PublicationType:
        if self.is_retracted:
            return PublicationType.RETRACTED
        if self.source is EvidenceSource.CLINICALTRIALS_GOV:
            return PublicationType.REGISTRY_RECORD
        lowered = [p.lower() for p in self.publication_types]
        for design, needles in _DESIGN_RULES:
            if any(any(n in p for n in needles) for p in lowered):
                return design
        if self.is_preprint:
            return PublicationType.PREPRINT

        haystack = f"{self.title} {self.abstract}".lower()
        if any(cue in haystack for cue in _PRECLINICAL_CUES) and not any(
            cue in haystack for cue in _CLINICAL_CUES
        ):
            return PublicationType.PRECLINICAL
        if any(cue in haystack for cue in _CLINICAL_CUES):
            return PublicationType.OBSERVATIONAL
        return PublicationType.OTHER

    @property
    def dedupe_key(self) -> str:
        """Cross-source identity key so the same paper is not counted twice."""
        if self.doi:
            return f"doi:{self.doi.lower()}"
        if self.pmid:
            return f"pmid:{self.pmid}"
        if self.nct_id:
            return f"nct:{self.nct_id.upper()}"
        return f"{self.source.value}:{self.external_id}"

    @property
    def citation_label(self) -> str:
        if self.pmid:
            return f"PMID:{self.pmid}"
        if self.nct_id:
            return self.nct_id
        if self.doi:
            return f"doi:{self.doi}"
        return f"{self.source.value}:{self.external_id}"

    @property
    def age_years(self) -> float | None:
        if not self.publication_year:
            return None
        return max(0.0, dt.datetime.now(dt.UTC).year - self.publication_year)

    @property
    def has_usable_abstract(self) -> bool:
        return len(self.abstract.strip()) >= 120

    def short_citation(self) -> str:
        # Both PubMed and Europe PMC render authors surname-first ("Smith JA").
        first_author = self.authors[0] if self.authors else ""
        surname = first_author.split()[0] if first_author.strip() else "Unknown"
        year = self.publication_year or "n.d."
        venue = self.journal or self.source.value.replace("_", " ").title()
        return f"{surname} et al., {venue} ({year}); {self.citation_label}"

    def to_prompt_dict(self, ref: str, *, abstract_limit: int = 2200) -> dict[str, Any]:
        """Compact representation handed to the adjudication prompt."""
        abstract = self.abstract.strip()
        truncated = len(abstract) > abstract_limit
        return {
            "ref": ref,
            "source": self.source.value,
            "id": self.citation_label,
            "title": self.title,
            "journal": self.journal,
            "year": self.publication_year,
            "publication_types": self.publication_types[:6],
            "abstract": abstract[:abstract_limit] + (" [truncated]" if truncated else ""),
            "abstract_available": bool(abstract),
            "is_retracted": self.is_retracted,
            "is_preprint": self.is_preprint,
            "trial": self.trial or None,
        }
