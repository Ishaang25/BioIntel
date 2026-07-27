"""PubMed client (NCBI E-utilities).

Two-step protocol: ``esearch`` resolves a query to PMIDs, ``efetch`` returns
the full records as XML.  We parse the XML rather than using the JSON summary
endpoint because only ``efetch`` returns abstracts, MeSH terms and publication
types -- all of which the adjudication and scoring stages depend on.
"""

from __future__ import annotations

import re
from typing import Any
from xml.etree import ElementTree as ET

from app.core.config import settings
from app.core.enums import EvidenceSource
from app.core.logging import get_logger
from app.evidence.http import SourceClient
from app.evidence.models import EvidenceRecord
from app.utils.text import collapse_whitespace

log = get_logger(__name__)

#: NCBI permits 3 requests/second anonymously and 10 with an API key, but it
#: measures bursts strictly -- observed 429s at 2.8/s. Stay comfortably under.
_RATE_WITH_KEY = 8.0
_RATE_WITHOUT_KEY = 2.0

_RETRACTION_TYPES = {"retracted publication", "retraction of publication"}


class PubMedClient(SourceClient):
    source_name = "pubmed"

    def __init__(self, client: Any | None = None) -> None:
        self.rate_per_second = _RATE_WITH_KEY if settings.ncbi_api_key else _RATE_WITHOUT_KEY
        super().__init__(client)

    def _common_params(self) -> dict[str, Any]:
        params: dict[str, Any] = {
            "db": "pubmed",
            "tool": "BioIntel",
            "email": settings.ncbi_tool_email,
        }
        if settings.ncbi_api_key:
            params["api_key"] = settings.ncbi_api_key
        return params

    async def search_ids(self, query: str, *, limit: int | None = None) -> list[str]:
        params = {
            **self._common_params(),
            "term": query,
            "retmax": limit or settings.retrieval_page_size,
            "retmode": "json",
            "sort": "relevance",
        }
        payload = await self.get(f"{settings.pubmed_base_url}/esearch.fcgi", params)
        result = (payload or {}).get("esearchresult", {})
        ids = result.get("idlist") or []
        log.debug("pubmed.search", query=query[:160], hits=len(ids), total=result.get("count"))
        return [str(pmid) for pmid in ids]

    async def fetch(self, pmids: list[str]) -> list[EvidenceRecord]:
        if not pmids:
            return []
        params = {
            **self._common_params(),
            "id": ",".join(pmids[:200]),
            "retmode": "xml",
        }
        xml = await self.get(f"{settings.pubmed_base_url}/efetch.fcgi", params, parse="text")
        return parse_pubmed_xml(xml)

    async def search(self, query: str, *, limit: int | None = None) -> list[EvidenceRecord]:
        ids = await self.search_ids(query, limit=limit)
        records = await self.fetch(ids)
        for record in records:
            record.retrieval_query = query
        return records


# ------------------------------------------------------------------ parsing ---
def _text(node: ET.Element | None) -> str:
    if node is None:
        return ""
    return collapse_whitespace("".join(node.itertext()))


def _abstract_text(article: ET.Element) -> str:
    """Join structured abstract sections, preserving their labels."""
    parts: list[str] = []
    for element in article.findall(".//Abstract/AbstractText"):
        label = element.get("Label") or element.get("NlmCategory")
        body = collapse_whitespace("".join(element.itertext()))
        if not body:
            continue
        parts.append(f"{label.strip().title()}: {body}" if label else body)
    if not parts:
        other = article.find(".//OtherAbstract/AbstractText")
        if other is not None:
            parts.append(_text(other))
    return " ".join(parts).strip()


def _authors(article: ET.Element) -> list[str]:
    authors: list[str] = []
    for author in article.findall(".//AuthorList/Author"):
        last = _text(author.find("LastName"))
        initials = _text(author.find("Initials"))
        collective = _text(author.find("CollectiveName"))
        if collective:
            authors.append(collective)
        elif last:
            authors.append(f"{last} {initials}".strip())
    return authors


_YEAR_RE = re.compile(r"\b(1[89]\d{2}|20\d{2})\b")


def _publication_year(article: ET.Element) -> int | None:
    for path in (
        ".//Journal/JournalIssue/PubDate/Year",
        ".//ArticleDate/Year",
        ".//PubMedPubDate[@PubStatus='pubmed']/Year",
    ):
        value = _text(article.find(path))
        if value.isdigit():
            return int(value)
    medline = _text(article.find(".//Journal/JournalIssue/PubDate/MedlineDate"))
    match = _YEAR_RE.search(medline)
    return int(match.group(1)) if match else None


def _publication_date(article: ET.Element) -> str | None:
    year = _text(article.find(".//Journal/JournalIssue/PubDate/Year"))
    month = _text(article.find(".//Journal/JournalIssue/PubDate/Month"))
    day = _text(article.find(".//Journal/JournalIssue/PubDate/Day"))
    parts = [p for p in (year, month, day) if p]
    return "-".join(parts) if parts else None


def parse_pubmed_xml(xml: str) -> list[EvidenceRecord]:
    """Parse an ``efetch`` XML payload into normalised records."""
    if not xml or not xml.strip():
        return []
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        log.warning("pubmed.xml_parse_failed", error=str(exc)[:200])
        return []

    records: list[EvidenceRecord] = []
    for article in root.findall(".//PubmedArticle"):
        pmid = _text(article.find(".//MedlineCitation/PMID"))
        if not pmid:
            continue

        publication_types = [
            _text(node) for node in article.findall(".//PublicationTypeList/PublicationType")
        ]
        lowered = {p.lower() for p in publication_types}
        is_retracted = bool(lowered & _RETRACTION_TYPES)

        doi = None
        pmcid = None
        for ident in article.findall(".//ArticleIdList/ArticleId"):
            id_type = (ident.get("IdType") or "").lower()
            value = _text(ident)
            if id_type == "doi" and value:
                doi = value
            elif id_type == "pmc" and value:
                pmcid = value

        mesh_terms = [
            _text(node) for node in article.findall(".//MeshHeadingList/MeshHeading/DescriptorName")
        ]
        keywords = [_text(node) for node in article.findall(".//KeywordList/Keyword")]

        records.append(
            EvidenceRecord(
                source=EvidenceSource.PUBMED,
                external_id=pmid,
                pmid=pmid,
                pmcid=pmcid,
                doi=doi,
                title=_text(article.find(".//ArticleTitle")),
                abstract=_abstract_text(article),
                journal=_text(article.find(".//Journal/Title"))
                or _text(article.find(".//Journal/ISOAbbreviation"))
                or None,
                publication_year=_publication_year(article),
                publication_date=_publication_date(article),
                authors=_authors(article),
                publication_types=publication_types,
                mesh_terms=[m for m in mesh_terms if m],
                keywords=[k for k in keywords if k],
                url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
                is_retracted=is_retracted,
                is_preprint="preprint" in lowered,
            )
        )
    return records
