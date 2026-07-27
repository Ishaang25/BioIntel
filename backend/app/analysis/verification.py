"""Authoritative verification of regulatory and pipeline claims.

The literature cannot answer "is this approved?" or "is this really in Phase
3?".  PubMed does not index approvals, and searching it for a regulatory claim
returns topically-related papers that say nothing about the claim -- which is
how an FDA-approved product came to be reported as unsupported.

This stage routes those claims to sources that can actually settle them:

* **openFDA / Drugs@FDA** for U.S. approval status and label population.
* **ClinicalTrials.gov** for development stage, sponsor and trial existence.

The controlling principle is that a null result from an authoritative source
is reported honestly.  Three outcomes are carefully distinguished:

``VERIFIED``
    The source confirms the claim.
``REFUTED``
    The source positively disagrees -- e.g. the deck says Phase 3 and every
    registered trial for that asset is Phase 1.  This is a real finding.
``NOT_FOUND`` / ``SOURCE_UNAVAILABLE``
    We could not check.  Mapped to ``NOT_INDEPENDENTLY_VERIFIED``, with the
    reason stated, and it never reduces a score.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field

from app.core.config import settings
from app.core.enums import (
    ClaimType,
    CorroborationStatus,
    VerificationStatus,
)
from app.core.logging import get_logger
from app.evidence.clinicaltrials import ClinicalTrialsClient
from app.evidence.models import EvidenceRecord
from app.evidence.openfda import FDAApproval, OpenFDAClient
from app.utils.text import collapse_whitespace

log = get_logger(__name__)

#: Claim types worth spending an authoritative lookup on.
VERIFIABLE_TYPES = frozenset(
    {
        ClaimType.REGULATORY_APPROVAL,
        ClaimType.REGULATORY_SUBMISSION,
        ClaimType.PIPELINE_STAGE,
        ClaimType.PARTNERSHIP,
    }
)

_APPROVAL_MARKERS = ("approved", "approval", "licensed", "authorized", "authorised", "cleared")
_SUBMISSION_MARKERS = ("filed", "submitted", "pdufa", "bla", "nda", "maa", "under review")

#: Matches "Phase 3", "Phase III", "Phase 1/2", "Phase I/II" and the
#: sub-phase forms decks use constantly ("Phase 2b", "Phase 1b").  The
#: optional trailing letter must be consumed here: without it "Phase 2b"
#: fails the word boundary and the phase is silently missed.
_PHASE_RE = re.compile(r"\bphase\s*(1/2|2/3|i/ii|ii/iii|1|2|3|4|iv|iii|ii|i)[ab]?\b", re.IGNORECASE)
_PHASE_CANONICAL = {
    "1": "1",
    "i": "1",
    "2": "2",
    "ii": "2",
    "3": "3",
    "iii": "3",
    "4": "4",
    "iv": "4",
    "1/2": "1/2",
    "i/ii": "1/2",
    "2/3": "2/3",
    "ii/iii": "2/3",
}


@dataclass(slots=True)
class VerificationResult:
    """The outcome of trying to settle one claim against an authority."""

    claim_id: str
    status: VerificationStatus
    #: Which authority was consulted ("openfda", "clinicaltrials_gov", ...).
    source: str
    #: Analyst-facing explanation. Always populated, including for null results.
    detail: str
    #: Identifiers that back the finding (BLA numbers, NCT ids).
    identifiers: list[str] = field(default_factory=list)
    #: Registry/label records promoted into the evidence set.
    records: list[EvidenceRecord] = field(default_factory=list)
    #: Confidence in the verification itself, 0-1.
    confidence: float = 0.0

    @property
    def corroboration(self) -> CorroborationStatus:
        """Map the verification outcome onto a corroboration status.

        This is the mapping that fixes the Moderna failure: a source that
        does not cover the product yields NOT_INDEPENDENTLY_VERIFIED, never
        a finding against the company.
        """
        return {
            VerificationStatus.VERIFIED: CorroborationStatus.CORROBORATED,
            VerificationStatus.PARTIALLY_VERIFIED: CorroborationStatus.PARTIALLY_CORROBORATED,
            VerificationStatus.REFUTED: CorroborationStatus.CONTRADICTED,
            VerificationStatus.NOT_FOUND: CorroborationStatus.NOT_INDEPENDENTLY_VERIFIED,
            VerificationStatus.SOURCE_UNAVAILABLE: CorroborationStatus.NOT_INDEPENDENTLY_VERIFIED,
            VerificationStatus.NOT_ATTEMPTED: CorroborationStatus.NOT_INDEPENDENTLY_VERIFIED,
        }[self.status]

    @property
    def is_decisive(self) -> bool:
        return self.status in (
            VerificationStatus.VERIFIED,
            VerificationStatus.PARTIALLY_VERIFIED,
            VerificationStatus.REFUTED,
        )


@dataclass(slots=True)
class VerificationRequest:
    claim_id: str
    claim_type: ClaimType
    statement: str
    #: Product / asset names mentioned in the claim, already alias-expanded.
    product_names: list[str] = field(default_factory=list)
    company_name: str | None = None
    #: Trial phase the deck asserts, if any ("1", "2", "3", "1/2"...).
    asserted_phase: str | None = None
    indication: str | None = None


class RegulatoryVerifier:
    """Resolves regulatory and pipeline claims against authoritative sources."""

    def __init__(
        self,
        *,
        openfda: OpenFDAClient | None = None,
        clinicaltrials: ClinicalTrialsClient | None = None,
    ) -> None:
        self.openfda = openfda or OpenFDAClient()
        self.clinicaltrials = clinicaltrials or ClinicalTrialsClient()
        self._semaphore = asyncio.Semaphore(settings.retrieval_concurrency)

    async def verify_many(
        self, requests: list[VerificationRequest]
    ) -> dict[str, VerificationResult]:
        if not settings.regulatory_verification_enabled or not requests:
            return {}

        selected = requests[: settings.max_verification_claims]

        async def bounded(request: VerificationRequest) -> VerificationResult:
            async with self._semaphore:
                try:
                    return await self.verify(request)
                except Exception as exc:
                    log.warning(
                        "verification.failed", claim_id=request.claim_id, error=str(exc)[:200]
                    )
                    return VerificationResult(
                        claim_id=request.claim_id,
                        status=VerificationStatus.SOURCE_UNAVAILABLE,
                        source="none",
                        detail=(
                            "The authoritative source could not be reached for this claim; "
                            "it is recorded as not independently verified."
                        ),
                    )

        results = await asyncio.gather(*(bounded(r) for r in selected))
        return {result.claim_id: result for result in results}

    async def verify(self, request: VerificationRequest) -> VerificationResult:
        if request.claim_type is ClaimType.REGULATORY_APPROVAL:
            return await self._verify_approval(request)
        if request.claim_type in (ClaimType.PIPELINE_STAGE, ClaimType.REGULATORY_SUBMISSION):
            return await self._verify_development_stage(request)
        if request.claim_type is ClaimType.PARTNERSHIP:
            return await self._verify_partnership(request)
        return VerificationResult(
            claim_id=request.claim_id,
            status=VerificationStatus.NOT_ATTEMPTED,
            source="none",
            detail="No authoritative source covers this kind of claim.",
        )

    # ------------------------------------------------------------ approvals ---
    async def _verify_approval(self, request: VerificationRequest) -> VerificationResult:
        approvals: list[FDAApproval] = []
        for name in request.product_names[:4]:
            found = await self.openfda.search_approvals(name, limit=5)
            approvals.extend(found)
            if found:
                break

        if approvals:
            matched = [
                a for a in approvals if any(a.matches_name(n) for n in request.product_names)
            ]
            best = matched or approvals
            identifiers = [a.application_number for a in best if a.application_number]
            detail = (
                "Confirmed against the FDA's Drugs@FDA database: "
                + "; ".join(a.label() for a in best[:3])
                + "."
            )
            status = (
                VerificationStatus.VERIFIED if matched else VerificationStatus.PARTIALLY_VERIFIED
            )
            if not matched:
                detail += (
                    " The application was found under a related name rather than the exact "
                    "product named in the deck; confirm the mapping."
                )
            return VerificationResult(
                claim_id=request.claim_id,
                status=status,
                source="openfda",
                detail=detail,
                identifiers=identifiers,
                confidence=0.92 if matched else 0.65,
            )

        # Not found. Distinguish a genuine gap from a dataset coverage limit:
        # Drugs@FDA does not index CBER-licensed vaccines, so an absent vaccine
        # is uninformative and must not read as a negative finding.
        looks_like_vaccine = _looks_like_vaccine(request.statement, request.product_names)
        if looks_like_vaccine:
            detail = (
                "Not found in the FDA's Drugs@FDA dataset. That dataset does not index "
                "CBER-licensed vaccines, so this is a known coverage gap rather than "
                "evidence against the claim. Confirm against the FDA's biologics "
                "licence list or the approval letter."
            )
        else:
            detail = (
                "No matching application was found in the FDA's Drugs@FDA dataset under the "
                "names given in the deck. This may reflect a naming mismatch, a non-U.S. "
                "approval, or a product licensed through a route this dataset does not cover. "
                "Request the approval letter."
            )
        return VerificationResult(
            claim_id=request.claim_id,
            status=VerificationStatus.NOT_FOUND,
            source="openfda",
            detail=detail,
            confidence=0.0,
        )

    # ------------------------------------------------------ development stage ---
    async def _verify_development_stage(self, request: VerificationRequest) -> VerificationResult:
        records: list[EvidenceRecord] = []
        for name in request.product_names[:3]:
            found = await self.clinicaltrials.search(interventions=[name], limit=25)
            records.extend(found)
            if found:
                break

        if not records and request.indication:
            records = await self.clinicaltrials.search(
                conditions=[request.indication], terms=request.company_name, limit=25
            )

        if not records:
            return VerificationResult(
                claim_id=request.claim_id,
                status=VerificationStatus.NOT_FOUND,
                source="clinicaltrials_gov",
                detail=(
                    "No registered trial matched the asset names in this claim. "
                    "Interventional studies must be registered, so confirm the trial "
                    "identifier with the company; a naming mismatch is the most likely "
                    "explanation."
                ),
            )

        observed = _highest_phase(records)
        asserted = request.asserted_phase
        identifiers = [r.nct_id for r in records[:5] if r.nct_id]

        if asserted is None:
            return VerificationResult(
                claim_id=request.claim_id,
                status=VerificationStatus.PARTIALLY_VERIFIED,
                source="clinicaltrials_gov",
                detail=(
                    f"{len(records)} registered trial(s) found for this asset"
                    + (f"; the most advanced is Phase {observed}." if observed else ".")
                ),
                identifiers=identifiers,
                records=records[:5],
                confidence=0.6,
            )

        if observed is None:
            return VerificationResult(
                claim_id=request.claim_id,
                status=VerificationStatus.PARTIALLY_VERIFIED,
                source="clinicaltrials_gov",
                detail=(
                    f"{len(records)} registered trial(s) confirm the programme exists, but none "
                    "state a phase, so the asserted stage could not be checked."
                ),
                identifiers=identifiers,
                records=records[:5],
                confidence=0.45,
            )

        if _phase_rank(observed) >= _phase_rank(asserted):
            return VerificationResult(
                claim_id=request.claim_id,
                status=VerificationStatus.VERIFIED,
                source="clinicaltrials_gov",
                detail=(
                    f"ClinicalTrials.gov confirms the asserted Phase {asserted} stage: the most "
                    f"advanced registered study for this asset is Phase {observed} "
                    f"({', '.join(identifiers[:3])})."
                ),
                identifiers=identifiers,
                records=records[:5],
                confidence=0.88,
            )

        # The registry positively disagrees. This is a genuine finding.
        return VerificationResult(
            claim_id=request.claim_id,
            status=VerificationStatus.REFUTED,
            source="clinicaltrials_gov",
            detail=(
                f"The deck asserts Phase {asserted}, but the most advanced registered study for "
                f"this asset is Phase {observed} ({', '.join(identifiers[:3])}). Either a trial "
                "has not been registered, or the stage is overstated."
            ),
            identifiers=identifiers,
            records=records[:5],
            confidence=0.72,
        )

    # ---------------------------------------------------------- partnerships ---
    async def _verify_partnership(self, request: VerificationRequest) -> VerificationResult:
        if not request.company_name:
            return VerificationResult(
                claim_id=request.claim_id,
                status=VerificationStatus.NOT_ATTEMPTED,
                source="none",
                detail="No company name was available to check collaboration records against.",
            )

        records: list[EvidenceRecord] = []
        for name in request.product_names[:2]:
            records.extend(await self.clinicaltrials.search(interventions=[name], limit=10))
            if records:
                break

        sponsors = {str((r.trial or {}).get("sponsor") or "").lower() for r in records if r.trial}
        sponsors.discard("")
        needle = request.company_name.lower()
        partner_named = any(needle in s or s in needle for s in sponsors)

        if records and partner_named:
            return VerificationResult(
                claim_id=request.claim_id,
                status=VerificationStatus.VERIFIED,
                source="clinicaltrials_gov",
                detail=(
                    "Registered trials for this asset name the stated party as sponsor or "
                    "collaborator: " + "; ".join(sorted(sponsors)[:3]) + "."
                ),
                identifiers=[r.nct_id for r in records[:3] if r.nct_id],
                records=records[:3],
                confidence=0.8,
            )
        if records:
            return VerificationResult(
                claim_id=request.claim_id,
                status=VerificationStatus.PARTIALLY_VERIFIED,
                source="clinicaltrials_gov",
                detail=(
                    "Registered trials exist for this asset but the stated partner is not named "
                    "as a sponsor: "
                    + "; ".join(sorted(sponsors)[:3] or ["no sponsor listed"])
                    + ". The collaboration may be preclinical or unregistered."
                ),
                identifiers=[r.nct_id for r in records[:3] if r.nct_id],
                records=records[:3],
                confidence=0.4,
            )
        return VerificationResult(
            claim_id=request.claim_id,
            status=VerificationStatus.NOT_FOUND,
            source="clinicaltrials_gov",
            detail="No registered trial was found under this asset name to check sponsorship.",
        )

    async def aclose(self) -> None:
        await asyncio.gather(
            self.openfda.aclose(), self.clinicaltrials.aclose(), return_exceptions=True
        )


# ------------------------------------------------------------------ helpers ---
def extract_asserted_phase(text: str) -> str | None:
    """The trial phase a claim asserts, canonicalised ('III' -> '3')."""
    match = _PHASE_RE.search(text or "")
    if not match:
        return None
    return _PHASE_CANONICAL.get(match.group(1).lower())


def _highest_phase(records: list[EvidenceRecord]) -> str | None:
    best: str | None = None
    for record in records:
        for phase in (record.trial or {}).get("phases") or []:
            canonical = _PHASE_CANONICAL.get(str(phase).lower().replace("phase", "").strip())
            if canonical is None:
                digits = re.sub(r"\D", "", str(phase))
                canonical = digits[:1] or None
            if canonical and (best is None or _phase_rank(canonical) > _phase_rank(best)):
                best = canonical
    return best


def _phase_rank(phase: str) -> int:
    return {"1": 1, "1/2": 2, "2": 3, "2/3": 4, "3": 5, "4": 6}.get(phase, 0)


_VACCINE_MARKERS = (
    "vaccine",
    "vaccination",
    "immunis",
    "immuniz",
    "prophyla",
    "booster",
    "mresvia",
    "spikevax",
    "comirnaty",
    "shingrix",
    "arexvy",
    "abrysvo",
)


def _looks_like_vaccine(statement: str, names: list[str]) -> bool:
    haystack = collapse_whitespace(f"{statement} {' '.join(names)}").lower()
    return any(marker in haystack for marker in _VACCINE_MARKERS)


def is_approval_claim(text: str) -> bool:
    lowered = (text or "").lower()
    return any(marker in lowered for marker in _APPROVAL_MARKERS)


def is_submission_claim(text: str) -> bool:
    lowered = (text or "").lower()
    return any(marker in lowered for marker in _SUBMISSION_MARKERS)


def summarise(results: dict[str, VerificationResult]) -> dict[str, int | dict[str, int]]:
    by_status: dict[str, int] = {}
    by_source: dict[str, int] = {}
    for result in results.values():
        by_status[result.status.value] = by_status.get(result.status.value, 0) + 1
        by_source[result.source] = by_source.get(result.source, 0) + 1
    return {
        "attempted": len(results),
        "decisive": sum(1 for r in results.values() if r.is_decisive),
        "by_status": by_status,
        "by_source": by_source,
    }
