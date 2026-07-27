"""Europe PMC client.

Europe PMC complements PubMed: it indexes preprints (bioRxiv/medRxiv), patents
and agricultural/European literature that PubMed omits, and it returns citation
counts, which feed the evidence-quality score.
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

_PREPRINT_SOURCES = {"PPR"}


class EuropePMCClient(SourceClient):
    source_name = "europe_pmc"
    rate_per_second = 5.0

    async def search(self, query: str, *, limit: int | None = None) -> list[EvidenceRecord]:
        params = {
            "query": query,
            "format": "json",
            "resultType": "core",
            "pageSize": min(100, limit or settings.retrieval_page_size),
            # Default (relevance) ordering. Sorting by citation count surfaces
            # famous methodology papers instead of on-topic evidence.
        }
        payload = await self.get(f"{settings.europepmc_base_url}/search", params)
        results = ((payload or {}).get("resultList") or {}).get("result") or []
        records = [_to_record(item) for item in results]
        records = [r for r in records if r is not None]
        for record in records:
            record.retrieval_query = query  # type: ignore[union-attr]
        log.debug("europepmc.search", query=query[:160], hits=len(records))
        return records  # type: ignore[return-value]


def _to_record(item: dict[str, Any]) -> EvidenceRecord | None:
    external_id = str(item.get("id") or item.get("pmid") or item.get("doi") or "").strip()
    if not external_id:
        return None

    source_code = (item.get("source") or "").upper()
    pub_types = item.get("pubTypeList", {}).get("pubType", []) or []
    if isinstance(pub_types, str):
        pub_types = [pub_types]

    authors_raw = item.get("authorList", {}).get("author", []) or []
    authors = [
        collapse_whitespace(a.get("fullName") or a.get("lastName") or "")
        for a in authors_raw
        if isinstance(a, dict)
    ]

    mesh = [
        m.get("descriptorName", "")
        for m in (item.get("meshHeadingList", {}).get("meshHeading", []) or [])
        if isinstance(m, dict) and m.get("descriptorName")
    ]
    keywords = item.get("keywordList", {}).get("keyword", []) or []
    if isinstance(keywords, str):
        keywords = [keywords]

    pmid = str(item["pmid"]) if item.get("pmid") else None
    year = None
    if item.get("pubYear"):
        try:
            year = int(item["pubYear"])
        except (TypeError, ValueError):
            year = None

    url = None
    for link in item.get("fullTextUrlList", {}).get("fullTextUrl", []) or []:
        if isinstance(link, dict) and link.get("url"):
            url = link["url"]
            break
    if not url:
        url = f"https://europepmc.org/article/{source_code or 'MED'}/{external_id}"

    return EvidenceRecord(
        source=EvidenceSource.EUROPE_PMC,
        external_id=f"{source_code}:{external_id}" if source_code else external_id,
        title=collapse_whitespace(item.get("title", "")),
        abstract=collapse_whitespace(item.get("abstractText", "") or ""),
        journal=collapse_whitespace(item.get("journalTitle", "") or "") or None,
        publication_year=year,
        publication_date=item.get("firstPublicationDate"),
        authors=[a for a in authors if a],
        publication_types=[p for p in pub_types if p],
        mesh_terms=mesh,
        keywords=[k for k in keywords if isinstance(k, str)],
        doi=item.get("doi"),
        pmid=pmid,
        pmcid=item.get("pmcid"),
        url=url,
        citation_count=int(item["citedByCount"]) if item.get("citedByCount") is not None else None,
        is_preprint=source_code in _PREPRINT_SOURCES
        or any("preprint" in str(p).lower() for p in pub_types),
        raw={"source": source_code, "isOpenAccess": item.get("isOpenAccess")},
    )
