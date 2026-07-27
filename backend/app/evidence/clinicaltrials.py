"""ClinicalTrials.gov client (API v2).

Registry records answer questions the literature cannot: whether anyone has
actually taken this mechanism into humans, at what phase, with what enrolment,
what endpoints were pre-specified, and -- critically for diligence -- whether
prior trials at the same target were terminated or withdrawn.
"""

from __future__ import annotations

from typing import Any

from app.core.config import settings
from app.core.enums import EvidenceSource
from app.core.logging import get_logger
from app.evidence.http import SourceClient
from app.evidence.models import EvidenceRecord
from app.utils.text import collapse_whitespace

log = get_logger(__name__)

#: Statuses that are themselves a diligence signal.
NEGATIVE_STATUSES = {"TERMINATED", "WITHDRAWN", "SUSPENDED"}


class ClinicalTrialsClient(SourceClient):
    source_name = "clinicaltrials_gov"
    rate_per_second = 4.0

    async def search(
        self,
        *,
        conditions: list[str] | None = None,
        interventions: list[str] | None = None,
        terms: str | None = None,
        limit: int | None = None,
    ) -> list[EvidenceRecord]:
        params: dict[str, Any] = {
            "format": "json",
            "pageSize": min(100, limit or settings.retrieval_page_size),
            "countTotal": "true",
        }
        if conditions:
            params["query.cond"] = " OR ".join(conditions[:3])
        if interventions:
            params["query.intr"] = " OR ".join(interventions[:3])
        if terms:
            params["query.term"] = terms
        if not any(k.startswith("query.") for k in params):
            return []

        payload = await self.get(f"{settings.clinicaltrials_base_url}/studies", params)
        studies = (payload or {}).get("studies") or []
        records = [record for study in studies if (record := _to_record(study)) is not None]
        query_label = "; ".join(
            f"{k.split('.')[-1]}={v}" for k, v in params.items() if k.startswith("query.")
        )
        for record in records:
            record.retrieval_query = query_label
        log.debug("ctgov.search", query=query_label, hits=len(records))
        return records


def _to_record(study: dict[str, Any]) -> EvidenceRecord | None:
    protocol = study.get("protocolSection") or {}
    identification = protocol.get("identificationModule") or {}
    nct_id = identification.get("nctId")
    if not nct_id:
        return None

    status_module = protocol.get("statusModule") or {}
    design = protocol.get("designModule") or {}
    description = protocol.get("descriptionModule") or {}
    conditions = (protocol.get("conditionsModule") or {}).get("conditions") or []
    arms = protocol.get("armsInterventionsModule") or {}
    outcomes = protocol.get("outcomesModule") or {}
    sponsor = (protocol.get("sponsorCollaboratorsModule") or {}).get("leadSponsor") or {}

    phases = design.get("phases") or []
    enrollment_info = design.get("enrollmentInfo") or {}
    status = status_module.get("overallStatus", "")

    primary_outcomes = [
        collapse_whitespace(o.get("measure", ""))
        for o in (outcomes.get("primaryOutcomes") or [])
        if o.get("measure")
    ]
    interventions = [
        collapse_whitespace(f"{i.get('type', '')}: {i.get('name', '')}".strip(": "))
        for i in (arms.get("interventions") or [])
    ]

    start_date = (status_module.get("startDateStruct") or {}).get("date")
    completion_date = (status_module.get("primaryCompletionDateStruct") or {}).get("date")
    year = None
    if start_date:
        try:
            year = int(str(start_date)[:4])
        except ValueError:
            year = None

    summary = collapse_whitespace(description.get("briefSummary", ""))
    detailed = collapse_whitespace(description.get("detailedDescription", ""))
    abstract = " ".join(part for part in (summary, detailed) if part)

    has_results = bool(study.get("hasResults"))

    return EvidenceRecord(
        source=EvidenceSource.CLINICALTRIALS_GOV,
        external_id=nct_id,
        nct_id=nct_id,
        title=collapse_whitespace(
            identification.get("officialTitle") or identification.get("briefTitle") or nct_id
        ),
        abstract=abstract,
        journal="ClinicalTrials.gov",
        publication_year=year,
        publication_date=start_date,
        authors=[sponsor.get("name")] if sponsor.get("name") else [],
        publication_types=["Registry Record", *phases],
        keywords=conditions,
        url=f"https://clinicaltrials.gov/study/{nct_id}",
        trial={
            "status": status,
            "phases": phases,
            "enrollment": enrollment_info.get("count"),
            "enrollment_type": enrollment_info.get("type"),
            "study_type": design.get("studyType"),
            "allocation": (design.get("designInfo") or {}).get("allocation"),
            "masking": ((design.get("designInfo") or {}).get("maskingInfo") or {}).get("masking"),
            "conditions": conditions[:8],
            "interventions": interventions[:8],
            "primary_outcomes": primary_outcomes[:6],
            "sponsor": sponsor.get("name"),
            "start_date": start_date,
            "primary_completion_date": completion_date,
            "has_results": has_results,
            "why_stopped": status_module.get("whyStopped"),
            "is_negative_signal": status.upper() in NEGATIVE_STATUSES,
        },
        raw={"hasResults": has_results},
    )
