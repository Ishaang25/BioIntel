"""openFDA client — authoritative U.S. approval status.

A pitch deck's regulatory claims are the ones an investor most wants checked
and the ones the literature is worst at answering: PubMed does not index
approvals, so searching it for "is mRESVIA approved?" returns papers about RSV
vaccines and no answer.  That mismatch is what made BioIntel report an
FDA-approved product as "unsupported".

openFDA exposes the FDA's own datasets.  Two are relevant here:

* ``/drug/drugsfda.json`` — the Drugs@FDA application database: approval
  actions, sponsors, marketing status, brand and generic names.
* ``/drug/label.json`` — the current structured product labels, which carry
  the approved indication and population.

No API key is required (rate-limited to 240 requests/minute anonymously,
1000/minute with a free key).

**Coverage boundary, verified against the live API.**  Drugs@FDA covers CDER
products, including BLA biologics such as pembrolizumab (BLA125514).  It does
*not* cover CBER-licensed vaccines: SPIKEVAX, MRESVIA and COMIRNATY all return
404 from both ``drugsfda`` and ``label``.  A miss therefore means "not in this
dataset", never "the claim is false", and :mod:`app.analysis.verification`
maps it to ``NOT_INDEPENDENTLY_VERIFIED`` with the coverage gap stated in the
memo rather than to a negative finding.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.core.config import settings
from app.core.logging import get_logger
from app.evidence.http import SourceClient
from app.utils.text import collapse_whitespace

log = get_logger(__name__)


@dataclass(slots=True)
class FDAApproval:
    """One approval action recorded by the FDA."""

    application_number: str
    sponsor: str | None
    brand_names: list[str] = field(default_factory=list)
    generic_names: list[str] = field(default_factory=list)
    approval_dates: list[str] = field(default_factory=list)
    marketing_status: str | None = None
    submission_types: list[str] = field(default_factory=list)
    #: Indication text from the structured product label, when retrieved.
    indications: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def is_biologic(self) -> bool:
        return self.application_number.upper().startswith("BLA")

    @property
    def earliest_approval(self) -> str | None:
        return min(self.approval_dates) if self.approval_dates else None

    def label(self) -> str:
        names = self.brand_names or self.generic_names
        name = names[0] if names else "unnamed product"
        date = self.earliest_approval
        suffix = f", approved {date[:4]}" if date and len(date) >= 4 else ""
        return f"{name} ({self.application_number}{suffix})"

    def matches_name(self, needle: str) -> bool:
        key = _normalise(needle)
        if not key:
            return False
        return any(key == _normalise(n) for n in (*self.brand_names, *self.generic_names))


def _normalise(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"[^a-z0-9]+", "", value.lower())


class OpenFDAClient(SourceClient):
    source_name = "openfda"
    #: openFDA allows 240 req/min anonymously; stay well inside it.
    rate_per_second = 3.0

    def _auth(self) -> dict[str, Any]:
        return {"api_key": settings.openfda_api_key} if settings.openfda_api_key else {}

    async def search_approvals(
        self, name: str, *, sponsor: str | None = None, limit: int = 10
    ) -> list[FDAApproval]:
        """Look up approval records for a brand or generic name.

        Returns an empty list when the product is not in Drugs@FDA. That is
        common and expected for CBER-licensed vaccines, so callers must not
        read it as a negative finding.
        """
        cleaned = collapse_whitespace(name)
        if len(cleaned) < 3:
            return []

        # Clauses are joined with literal " OR ", not "+OR+": the query goes
        # through URL parameter encoding, which turns a literal '+' into %2B
        # and breaks the boolean. A space encodes to '+' and works.
        terms = [
            f'openfda.brand_name:"{cleaned}"',
            f'openfda.generic_name:"{cleaned}"',
            f'products.brand_name:"{cleaned}"',
        ]
        query = "(" + " OR ".join(terms) + ")"
        if sponsor:
            query += f' AND sponsor_name:"{collapse_whitespace(sponsor)}"'

        payload = await self._safe_get(
            f"{settings.openfda_base_url}/drug/drugsfda.json",
            {**self._auth(), "search": query, "limit": min(25, limit)},
        )
        results = (payload or {}).get("results") or []
        approvals = [_to_approval(item) for item in results]
        log.debug("openfda.search", name=cleaned, hits=len(approvals))
        return approvals

    async def search_by_sponsor(self, sponsor: str, *, limit: int = 50) -> list[FDAApproval]:
        """Every approval on record for a sponsor.

        Used to check portfolio-level claims such as "two approved products
        in the U.S." without needing to know the product names in advance.
        """
        cleaned = collapse_whitespace(sponsor)
        if len(cleaned) < 3:
            return []
        payload = await self._safe_get(
            f"{settings.openfda_base_url}/drug/drugsfda.json",
            {**self._auth(), "search": f'sponsor_name:"{cleaned}"', "limit": min(100, limit)},
        )
        return [_to_approval(item) for item in ((payload or {}).get("results") or [])]

    async def fetch_label_indication(self, name: str) -> str | None:
        """The approved indication text from the structured product label.

        This is what settles a population claim such as "approved for ages
        60+": the label states the approved population verbatim.
        """
        cleaned = collapse_whitespace(name)
        if len(cleaned) < 3:
            return None
        payload = await self._safe_get(
            f"{settings.openfda_base_url}/drug/label.json",
            {
                **self._auth(),
                "search": (f'openfda.brand_name:"{cleaned}" OR openfda.generic_name:"{cleaned}"'),
                "limit": 1,
            },
        )
        results = (payload or {}).get("results") or []
        if not results:
            return None
        record = results[0]
        for key in ("indications_and_usage", "purpose", "description"):
            value = record.get(key)
            if value:
                text = value[0] if isinstance(value, list) else str(value)
                return collapse_whitespace(text)[:4000]
        return None

    async def _safe_get(self, url: str, params: dict[str, Any]) -> dict[str, Any] | None:
        """GET that treats a 404 as 'no such record' rather than an error.

        openFDA returns 404 for an empty result set, which is a normal outcome
        of a verification attempt and must not fail the stage.
        """
        try:
            return await self.get(url, params)
        except Exception as exc:
            message = str(exc)
            if "404" in message or "NOT_FOUND" in message.upper():
                return None
            log.warning("openfda.request_failed", url=url, error=message[:200])
            return None


def _to_approval(item: dict[str, Any]) -> FDAApproval:
    openfda = item.get("openfda") or {}
    products = item.get("products") or []
    submissions = item.get("submissions") or []

    approval_dates = [
        s.get("submission_status_date")
        for s in submissions
        if s.get("submission_status") == "AP" and s.get("submission_status_date")
    ]
    brand_names = _unique(
        [*(openfda.get("brand_name") or []), *[p.get("brand_name") for p in products]]
    )
    generic_names = _unique(
        [*(openfda.get("generic_name") or []), *[p.get("active_ingredients") for p in products]]
    )

    return FDAApproval(
        application_number=item.get("application_number", ""),
        sponsor=item.get("sponsor_name"),
        brand_names=brand_names,
        generic_names=[g for g in generic_names if isinstance(g, str)],
        approval_dates=sorted(d for d in approval_dates if d),
        marketing_status=(products[0].get("marketing_status") if products else None),
        submission_types=_unique([s.get("submission_type") for s in submissions]),
        raw={"application_number": item.get("application_number")},
    )


def _unique(values: list[Any]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if not isinstance(value, str) or not value.strip():
            continue
        key = value.strip().lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(value.strip())
    return out
