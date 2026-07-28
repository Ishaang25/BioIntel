"""Deterministic offline provider.

BioIntel must be runnable and testable end to end without OpenAI credentials:
for CI, for local demos, and so that a provider outage degrades rather than
halts.  This provider produces *schema-valid, content-grounded* output using
the deterministic biomedical pattern library in :mod:`app.extraction.lexicon`.

It is explicitly **not** a simulation of model quality.  Every artefact it
produces is tagged so the UI and the report can say so, and the analysis is
labelled ``degraded`` in run metrics.

Determinism matters: given the same input it returns the same output, which
makes integration tests assert on real behaviour rather than on mocks.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from typing import Any

from pydantic import BaseModel

from app.core.enums import (
    ClaimCategory,
    ClaimType,
    ConfidenceLevel,
    CorroborationStatus,
    PublicationType,
    QuestionPriority,
    RiskCategory,
    RiskSeverity,
    Stance,
)
from app.extraction import lexicon
from app.llm import schemas as S
from app.llm.base import LLMRequest, LLMResponse, Usage
from app.utils.text import collapse_whitespace, split_sentences, truncate

STUB_NOTE = "Generated offline by BioIntel's deterministic analyser (no LLM provider configured)."


class StubProvider:
    """Schema-valid, content-grounded output without a network call."""

    name = "stub"

    async def complete_structured(self, request: LLMRequest) -> LLMResponse:
        started = time.perf_counter()
        payload = _dispatch(request)
        text = json.dumps(payload, ensure_ascii=False)
        return LLMResponse(
            text=text,
            model=f"stub/{request.model}",
            usage=Usage(input_tokens=len(request.user) // 4, output_tokens=len(text) // 4),
            latency_ms=int((time.perf_counter() - started) * 1000),
            provider=self.name,
        )

    async def embed(self, texts: list[str], *, model: str, dimensions: int) -> list[list[float]]:
        """Hashed bag-of-words embedding.

        Not semantically meaningful in the way a trained model is, but it is
        deterministic, stable and gives non-degenerate cosine similarities for
        lexically related biomedical text -- enough to exercise the ranking
        code paths honestly.
        """
        return [_hash_embedding(text, dimensions) for text in texts]

    async def aclose(self) -> None:
        return None


# --------------------------------------------------------------- embeddings ---
_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _hash_embedding(text: str, dimensions: int) -> list[float]:
    vector = [0.0] * dimensions
    tokens = _TOKEN_RE.findall(text.lower())
    if not tokens:
        return vector
    for token in tokens:
        if len(token) < 3:
            continue
        digest = hashlib.blake2b(token.encode(), digest_size=8).digest()
        index = int.from_bytes(digest[:4], "big") % dimensions
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        vector[index] += sign
    norm = sum(v * v for v in vector) ** 0.5
    if norm == 0:
        return vector
    return [v / norm for v in vector]


# ----------------------------------------------------------------- dispatch ---
def _dispatch(request: LLMRequest) -> dict[str, Any]:
    model: type[BaseModel] = request.schema_model
    context = request.context or {}
    handler = _HANDLERS.get(model.__name__)
    if handler is None:  # pragma: no cover - guarded by tests over all schemas
        raise NotImplementedError(f"StubProvider has no handler for {model.__name__}")
    payload = handler(context, request)
    # Validate here so a stub bug surfaces immediately rather than downstream.
    return model.model_validate(payload).model_dump(mode="json")


def _page_understanding(context: dict[str, Any], request: LLMRequest) -> dict[str, Any]:
    text: str = context.get("page_text", "") or ""
    page_number: int = int(context.get("page_number", 1))
    tables: list[dict[str, Any]] = context.get("tables", []) or []
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    title = lines[0][:200] if lines else None

    quantities = lexicon.find_quantities(text)
    data_points = [
        {
            "label": q["metric"].replace("_", " "),
            "value": q["value"],
            "unit": q["unit"] or None,
            "context": truncate(collapse_whitespace(q["span"]), 120),
            "is_read_from_axis": False,
        }
        for q in quantities[:12]
    ]

    visual_elements: list[dict[str, Any]] = []
    if context.get("has_images") or context.get("vector_drawing_count", 0) >= 12:
        visual_elements.append(
            {
                "kind": "other",
                "description": (
                    "A graphical element is present on this page. Offline mode cannot "
                    "interpret images; enable an LLM provider for chart and diagram reading."
                ),
                "axis_labels": [],
                "legend_entries": [],
                "is_data_bearing": False,
            }
        )

    summary_source = " ".join(split_sentences(text)[:3]) or "No extractable text on this page."
    return {
        "slide_title": title,
        "summary": truncate(summary_source, 600),
        "recovered_text": "",
        "visual_elements": visual_elements,
        "data_points": data_points,
        "tables": [
            {
                "title": f"Table {t.get('index', 0) + 1} (page {page_number})",
                "markdown": t.get("markdown", ""),
            }
            for t in tables
            if t.get("markdown")
        ],
        "contains_scientific_content": lexicon.looks_scientific(text),
        "legibility": 1.0 if text else 0.0,
    }


def _company_profile(context: dict[str, Any], request: LLMRequest) -> dict[str, Any]:
    text: str = context.get("document_text", "") or ""
    first_page: str = context.get("first_page_text", "") or ""
    lines = [ln.strip() for ln in first_page.splitlines() if ln.strip()]
    name = lines[0][:120] if lines else None

    diseases = lexicon.find_diseases(text)
    modalities = lexicon.find_modalities(text)
    stage_match = re.search(
        r"\b(pre-?seed|seed|series [a-e]|bridge|crossover)\b", text, re.IGNORECASE
    )
    raise_match = re.search(r"raising\s+\$?([0-9.]+\s?[mMbB]n?)", text)
    assets = [f"{m.group(1)}-{m.group(2)}" for m in lexicon.ASSET_CODE_RE.finditer(text)]

    return {
        "company_name": name,
        "one_liner": truncate(" ".join(split_sentences(first_page)[:2]), 300) or None,
        "founded_year": _first_int(re.findall(r"\bfounded in (\d{4})\b", text, re.I)),
        "headquarters": None,
        "company_stage": stage_match.group(1).title() if stage_match else None,
        "lead_program": assets[0] if assets else None,
        "lead_indication": diseases[0].canonical if diseases else None,
        "modality": modalities[0].canonical if modalities else None,
        "development_stage": lexicon.infer_evidence_tier(text).value,
        "pipeline": [
            {
                "name": asset,
                "indication": diseases[0].canonical if diseases else None,
                "modality": modalities[0].canonical if modalities else None,
                "stage": None,
                "target": None,
            }
            for asset in _dedupe(assets)[:5]
        ],
        "team": [],
        "total_raised": None,
        "current_raise": raise_match.group(1) if raise_match else None,
        "use_of_funds": None,
        "partnerships": [],
        "ip_position": None,
        "business_model": None,
        "source_pages": context.get("page_numbers", [])[:20],
    }


def _entities(context: dict[str, Any], request: LLMRequest) -> dict[str, Any]:
    pages: list[dict[str, Any]] = context.get("pages", []) or []
    found: dict[tuple[str, str], dict[str, Any]] = {}
    for page in pages:
        page_number = int(page.get("page_number", 0))
        text = page.get("text", "") or ""
        for hit in lexicon.find_all_entities(text):
            key = (hit.entity_type.value, hit.canonical.lower())
            record = found.setdefault(
                key,
                {
                    "entity_type": hit.entity_type.value,
                    "name": hit.surface,
                    "canonical_name": hit.canonical,
                    "aliases": [],
                    "description": None,
                    "role_in_program": None,
                    "source_pages": [],
                    "confidence": 0.55,
                },
            )
            if page_number and page_number not in record["source_pages"]:
                record["source_pages"].append(page_number)
            if (
                hit.surface.lower() != record["name"].lower()
                and hit.surface not in record["aliases"]
            ):
                record["aliases"].append(hit.surface)
    for record in found.values():
        # More mentions across pages -> more confidence, capped well below 1.
        record["confidence"] = round(min(0.8, 0.45 + 0.08 * len(record["source_pages"])), 3)
    return {"entities": list(found.values())}


def _claims(context: dict[str, Any], request: LLMRequest) -> dict[str, Any]:
    pages: list[dict[str, Any]] = context.get("pages", []) or []
    claims: list[dict[str, Any]] = []
    seen: set[str] = set()

    for page in pages:
        page_number = int(page.get("page_number", 0))
        text = page.get("text", "") or ""
        for sentence in split_sentences(text):
            if len(sentence) < 35 or len(sentence) > 600:
                continue
            # Keep non-scientific statements too, correctly typed. Knowing that
            # a third of a deck is promotional or forward-looking is itself a
            # finding; dropping those sentences hides it and biases the
            # disclosure-quality assessment.
            claim_type = _infer_claim_type(sentence, lexicon.infer_claim_category(sentence))
            if not lexicon.looks_scientific(sentence) and claim_type is ClaimType.OTHER:
                continue
            key = sentence.lower()[:120]
            if key in seen:
                continue
            seen.add(key)

            quantities = lexicon.find_quantities(sentence)
            entity_hits = lexicon.find_all_entities(sentence)
            category = lexicon.infer_claim_category(sentence)
            tier = lexicon.infer_evidence_tier(sentence)
            importance = _importance(sentence, category, bool(quantities), len(entity_hits))

            claims.append(
                {
                    "statement": sentence,
                    "verbatim_quote": sentence,
                    "page_number": page_number,
                    "from_visual": False,
                    "claim_type": _infer_claim_type(sentence, category).value,
                    "category": category.value,
                    "claimed_evidence_tier": tier.value,
                    "quantitative": [
                        {
                            "metric": q["metric"].replace("_", " "),
                            "value": q["value"],
                            "unit": q["unit"] or None,
                            "comparator": None,
                            "sample_size": None,
                            "p_value": None,
                            "model_system": None,
                        }
                        for q in quantities[:6]
                    ],
                    "entity_names": _dedupe([h.canonical for h in entity_hits])[:8],
                    "is_scientific": category not in (ClaimCategory.MARKET, ClaimCategory.OTHER),
                    "is_falsifiable": bool(quantities) or bool(entity_hits),
                    "hedging_language": lexicon.has_hedging(sentence),
                    "is_thesis_critical": importance >= 0.7,
                    "importance": importance,
                    "confidence": 0.5,
                }
            )

    claims.sort(key=lambda c: c["importance"], reverse=True)
    limit = int(context.get("max_claims", 40))
    return {"claims": claims[:limit]}


def _importance(sentence: str, category: ClaimCategory, has_numbers: bool, entities: int) -> float:
    score = 0.35
    weights = {
        ClaimCategory.CLINICAL_EFFICACY: 0.30,
        ClaimCategory.PRECLINICAL_EFFICACY: 0.22,
        ClaimCategory.MECHANISM: 0.18,
        ClaimCategory.TARGET_VALIDATION: 0.20,
        ClaimCategory.SAFETY: 0.18,
        ClaimCategory.BIOMARKER: 0.12,
        ClaimCategory.PLATFORM: 0.08,
    }
    score += weights.get(category, 0.02)
    if has_numbers:
        score += 0.15
    score += min(0.12, 0.04 * entities)
    if lexicon.has_puffery(sentence):
        score -= 0.05
    return round(max(0.0, min(1.0, score)), 3)


def _query_plan(context: dict[str, Any], request: LLMRequest) -> dict[str, Any]:
    claim: str = context.get("claim_statement", "") or ""
    entities: list[str] = context.get("entity_names", []) or []
    diseases = [h.canonical for h in lexicon.find_diseases(claim)]
    targets = [h.canonical for h in lexicon.find_targets(claim)]
    terms = _dedupe([*targets, *diseases, *entities])[:4]
    base = " AND ".join(f'"{t}"' for t in terms) if terms else _keywords(claim)

    queries = [
        {
            "query": base,
            "intent": "confirm",
            "rationale": "Direct lexical search for the entities named in the claim.",
        }
    ]
    if terms:
        queries.append(
            {
                "query": f"{base} AND (limitation OR failure OR negative OR contradictory)",
                "intent": "refute",
                "rationale": "Surfaces disconfirming reports about the same entities.",
            }
        )
    return {
        "pubmed_queries": queries,
        "trial_conditions": diseases[:2],
        "trial_interventions": targets[:2],
    }


def _keywords(text: str, limit: int = 6) -> str:
    stop = {
        "the",
        "and",
        "for",
        "with",
        "that",
        "this",
        "from",
        "have",
        "has",
        "are",
        "was",
        "our",
        "their",
        "which",
        "into",
        "using",
        "been",
        "will",
        "can",
        "may",
        "more",
        "than",
        "these",
        "those",
        "such",
        "also",
        "over",
        "when",
        "where",
    }
    words = [w for w in _TOKEN_RE.findall(text.lower()) if len(w) > 3 and w not in stop]
    return " ".join(_dedupe(words)[:limit])


def _batch_adjudication(context: dict[str, Any], request: LLMRequest) -> dict[str, Any]:
    claim: str = context.get("claim_statement", "") or ""
    records: list[dict[str, Any]] = context.get("evidence", []) or []
    claim_tokens = _content_tokens(claim)

    out: list[dict[str, Any]] = []
    for record in records:
        abstract = record.get("abstract", "") or ""
        title = record.get("title", "") or ""
        haystack = f"{title}. {abstract}"
        overlap = _jaccard(claim_tokens, _content_tokens(haystack))
        relevance = round(min(1.0, overlap * 3.0), 3)

        sentences = split_sentences(abstract) or split_sentences(title)
        best = max(
            sentences,
            key=lambda s: _jaccard(claim_tokens, _content_tokens(s)),
            default="",
        )
        negation = any(
            token in haystack.lower()
            for token in (
                "did not",
                "no significant",
                "failed to",
                "was not associated",
                "no effect",
                "unable to",
                "contrary",
            )
        )
        if relevance < 0.15:
            stance, strength, quote = Stance.UNRELATED, 0.0, ""
        elif negation:
            stance, strength, quote = Stance.CONTRADICTS, round(relevance * 0.6, 3), best
        elif relevance >= 0.35:
            stance, strength, quote = Stance.SUPPORTS, round(relevance * 0.7, 3), best
        else:
            stance, strength, quote = Stance.NEUTRAL, round(relevance * 0.4, 3), best

        out.append(
            {
                "evidence_ref": record.get("ref", ""),
                "stance": stance.value,
                "relevance": relevance,
                "strength": strength,
                "supporting_quote": quote if stance != Stance.UNRELATED else "",
                "rationale": (
                    "Offline lexical overlap assessment; not a semantic judgement. "
                    f"Token overlap with the claim: {overlap:.2f}."
                ),
                "caveats": [STUB_NOTE],
                "study_design": _guess_design(record).value,
                "addresses_claim_directly": relevance >= 0.45,
            }
        )
    return {"adjudications": out}


def _single_adjudication(context: dict[str, Any], request: LLMRequest) -> dict[str, Any]:
    """Adjudicate one record. The pipeline batches, but the contract allows one."""
    records = context.get("evidence", []) or []
    first = records[0] if records else {"ref": "E1", "title": "", "abstract": ""}
    batch = _batch_adjudication({**context, "evidence": [first]}, request)
    item = dict(batch["adjudications"][0])
    item.pop("evidence_ref", None)
    return item


def _guess_design(record: dict[str, Any]) -> PublicationType:
    haystack = " ".join(
        [
            record.get("title", "") or "",
            " ".join(record.get("publication_types", []) or []),
            record.get("source", "") or "",
        ]
    ).lower()
    rules = [
        (PublicationType.META_ANALYSIS, ("meta-analysis",)),
        (PublicationType.SYSTEMATIC_REVIEW, ("systematic review",)),
        (
            PublicationType.RANDOMIZED_TRIAL,
            ("randomized controlled trial", "randomised", "phase 3"),
        ),
        (PublicationType.CLINICAL_TRIAL, ("clinical trial", "phase 1", "phase 2")),
        (PublicationType.REGISTRY_RECORD, ("clinicaltrials_gov",)),
        (PublicationType.REVIEW, ("review",)),
        (PublicationType.PREPRINT, ("preprint", "biorxiv", "medrxiv")),
        (PublicationType.CASE_REPORT, ("case report",)),
        (PublicationType.PRECLINICAL, ("mice", "mouse", "in vitro", "rat")),
    ]
    for design, needles in rules:
        if any(n in haystack for n in needles):
            return design
    return PublicationType.OTHER


def _claim_verdict(context: dict[str, Any], request: LLMRequest) -> dict[str, Any]:
    supporting = int(context.get("supporting_count", 0))
    contradicting = int(context.get("contradicting_count", 0))
    total = int(context.get("evidence_count", 0))
    tier = context.get("claimed_evidence_tier", "none_stated")
    claim_type = context.get("claim_type", "other")
    status = context.get("null_status", CorroborationStatus.INSUFFICIENT_EVIDENCE.value)

    # Mirror the real system's discipline: nothing found is never a contradiction.
    if contradicting > supporting and contradicting > 0:
        resolved = CorroborationStatus.CONTRADICTED.value
    elif supporting > 0:
        resolved = CorroborationStatus.PARTIALLY_CORROBORATED.value
    elif total == 0:
        resolved = status
    else:
        resolved = CorroborationStatus.PLAUSIBLE_UNVERIFIED.value

    if total == 0:
        verdict = (
            "No external literature was retrieved for this claim, so it could not be "
            "corroborated independently. This reflects the search, not the truth of the "
            "claim."
        )
    else:
        verdict = (
            f"Offline lexical matching found {supporting} record(s) consistent with the claim "
            f"and {contradicting} that appear to conflict, out of {total} retrieved. This is a "
            "keyword-level comparison, not a scientific reading."
        )

    comparisons = []
    if total >= 2:
        comparisons.append(
            {
                "topic": "Overall consistency of the retrieved records",
                "agreement": (
                    f"{supporting} record(s) share vocabulary with the claim." if supporting else ""
                ),
                "disagreement": (
                    f"{contradicting} record(s) contain negating language." if contradicting else ""
                ),
                "quality_contrast": (
                    "Offline mode cannot compare study designs; enable a language-model "
                    "provider for evidence-quality contrast."
                ),
                "translatability": (
                    "Not assessed offline. Species, population and endpoint gaps were not "
                    "evaluated."
                ),
            }
        )

    return {
        "corroboration_status": resolved,
        "confidence": ConfidenceLevel.LOW.value,
        "confidence_reason": (
            "Produced by deterministic lexical matching rather than scientific reading; "
            "confidence cannot exceed low in this mode."
        ),
        "verdict": verdict,
        "comparisons": comparisons,
        "key_uncertainties": [
            "Semantic adjudication of the retrieved evidence was not performed (offline mode).",
            f"The deck's strongest stated evidence tier for this claim is '{tier}'.",
        ][:4],
        "novelty": 0.5 if total == 0 else round(max(0.0, 1.0 - min(1.0, total / 10.0)), 3),
        "translational_gap": None,
        "so_what": (
            f"This {str(claim_type).replace('_', ' ')} claim requires review by a qualified "
            "advisor before it can inform a decision."
        ),
    }


def _risks_and_questions(context: dict[str, Any], request: LLMRequest) -> dict[str, Any]:
    claims: list[dict[str, Any]] = context.get("claims", []) or []
    risks: list[dict[str, Any]] = []
    questions: list[dict[str, Any]] = []

    unsupported = [c for c in claims if c.get("supporting_count", 0) == 0][:4]
    contradicted = [c for c in claims if c.get("contradicting_count", 0) > 0][:4]
    hedged = [c for c in claims if c.get("hedging_language")][:3]

    for claim in contradicted:
        risks.append(
            {
                "title": "Retrieved literature appears to conflict with a stated claim",
                "category": RiskCategory.SCIENTIFIC.value,
                "severity": RiskSeverity.HIGH.value,
                "description": (
                    "At least one retrieved record contains disconfirming language relevant to "
                    f'this claim: "{truncate(claim.get("statement", ""), 180)}" Manual review '
                    "of the cited records is required."
                ),
                "related_claim_refs": [claim.get("ref", "")],
            }
        )
    for claim in unsupported:
        risks.append(
            {
                "title": "Claim not corroborated by retrieved literature",
                "category": RiskCategory.SCIENTIFIC.value,
                "severity": RiskSeverity.MEDIUM.value,
                "description": (
                    "No external record was matched to this claim, so it currently rests on the "
                    f'company\'s assertion alone: "{truncate(claim.get("statement", ""), 180)}"'
                ),
                "related_claim_refs": [claim.get("ref", "")],
            }
        )
    for claim in hedged:
        risks.append(
            {
                "title": "Hedged language around a material claim",
                "category": RiskCategory.DATA_INTEGRITY.value,
                "severity": RiskSeverity.LOW.value,
                "description": (
                    "The deck uses hedging language, which can mask the absence of data: "
                    f'"{truncate(claim.get("statement", ""), 180)}"'
                ),
                "related_claim_refs": [claim.get("ref", "")],
            }
        )
    if not risks:
        risks.append(
            {
                "title": "Automated risk detection produced no findings",
                "category": RiskCategory.SCIENTIFIC.value,
                "severity": RiskSeverity.INFO.value,
                "description": STUB_NOTE,
                "related_claim_refs": [],
            }
        )

    templates: list[tuple[str, str, QuestionPriority, RiskCategory, str]] = [
        (
            "What primary data underpin {subject}, and can we review the raw datasets?",
            "The deck states the result but the underlying dataset determines whether it replicates.",
            QuestionPriority.CRITICAL,
            RiskCategory.SCIENTIFIC,
            "Raw data, n per group, statistical plan, and independent replication.",
        ),
        (
            "Which experiments would falsify {subject}, and have any been run?",
            "Falsification attempts distinguish a validated hypothesis from a hopeful one.",
            QuestionPriority.HIGH,
            RiskCategory.SCIENTIFIC,
            "A pre-specified failure criterion and evidence it was tested.",
        ),
        (
            "How does the translational model behind {subject} predict human response?",
            "Model-to-human translation is the dominant failure mode in biotech.",
            QuestionPriority.HIGH,
            RiskCategory.TRANSLATIONAL,
            "Precedent of the model predicting clinical outcome in this indication.",
        ),
        (
            "What is the competitive position of {subject} versus the current standard of care?",
            "Efficacy in isolation does not establish commercial or clinical differentiation.",
            QuestionPriority.MEDIUM,
            RiskCategory.COMPETITIVE,
            "Head-to-head or matched-cohort comparison against named competitors.",
        ),
    ]
    ranked = sorted(claims, key=lambda c: c.get("importance", 0.0), reverse=True)[:5]
    for claim in ranked:
        subject = truncate(collapse_whitespace(claim.get("statement", "")), 120)
        for template, rationale, priority, category, good in templates:
            questions.append(
                {
                    "question": template.format(subject=f'"{subject}"'),
                    "rationale": rationale,
                    "priority": priority.value,
                    "category": category.value,
                    "what_good_looks_like": good,
                    "related_claim_refs": [claim.get("ref", "")],
                }
            )
    return {"risks": risks[:10], "questions": questions[:15]}


def _report(context: dict[str, Any], request: LLMRequest) -> dict[str, Any]:
    company = context.get("company_name") or "the company"
    claim_count = int(context.get("claim_count", 0))
    evidence_count = int(context.get("evidence_count", 0))
    supported = int(context.get("supported_claims", 0))
    contradicted = int(context.get("contradicted_claims", 0))
    unsupported = int(context.get("unsupported_claims", 0))
    score = float(context.get("overall_score", 0.0))
    section_plan: list[dict[str, str]] = context.get("section_plan", []) or []

    summary = {
        "investment_thesis": (
            f"{company} presents {claim_count} extractable scientific claims. This summary was "
            "assembled offline by deterministic rules, so it indexes the deck rather than "
            "interpreting it."
        ),
        "key_strengths": [
            f"{supported} claim(s) matched at least one consistent external record.",
            f"{evidence_count} external record(s) were retrieved and linked.",
        ],
        "key_risks": [
            f"{contradicted} claim(s) matched at least one conflicting record.",
            f"{unsupported} claim(s) had no external match; this is an information gap.",
            STUB_NOTE,
        ],
        "recommendation_line": (
            f"Composite scientific credibility is {score:.0f}/100; re-run with a language-model "
            "provider before this informs a decision."
        ),
        "diligence_priorities": [
            "Configure a language-model provider and re-run the analysis.",
            "Request primary datasets for the claims with no external match.",
            "Have a qualified scientific advisor review the extracted claim table.",
        ],
    }

    sections = [
        {
            "heading": item.get("heading", "Section"),
            "body_markdown": (
                f"_{item.get('instruction', '')}_\n\n"
                f"{STUB_NOTE} Configure `OPENAI_API_KEY` and re-run to generate this section."
            ),
            "so_what": (
                "Not available offline: narrative analysis requires a language-model provider."
            ),
            "confidence": ConfidenceLevel.LOW.value,
            "confidence_reason": STUB_NOTE,
            "citation_refs": [],
        }
        for item in section_plan
    ]

    return {
        "title": f"Scientific Due Diligence — {company}",
        "executive_summary": summary,
        "sections": sections,
        "recommendation": (
            "Re-run this analysis with a language-model provider configured before using it in "
            "an investment committee. In the meantime, use the extracted claim and evidence "
            "tables as a checklist for expert review."
        ),
        "limitations": [
            STUB_NOTE,
            "Charts, diagrams and scanned pages were not interpreted.",
            "Evidence stance was assigned by lexical overlap, not scientific reading.",
        ],
    }


_HANDLERS = {
    S.PageUnderstandingOut.__name__: _page_understanding,
    S.CompanyProfileOut.__name__: _company_profile,
    S.EntityExtractionOut.__name__: _entities,
    S.ClaimExtractionOut.__name__: _claims,
    S.QueryPlanOut.__name__: _query_plan,
    S.BatchAdjudicationOut.__name__: _batch_adjudication,
    S.AdjudicationOut.__name__: _single_adjudication,
    S.ClaimVerdictOut.__name__: _claim_verdict,
    S.RisksAndQuestionsOut.__name__: _risks_and_questions,
    S.ReportOut.__name__: _report,
}


# ------------------------------------------------------------------ helpers ---
def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        key = value.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(value)
    return out


def _first_int(values: list[str]) -> int | None:
    for value in values:
        try:
            return int(value)
        except ValueError:
            continue
    return None


_STOPWORDS = frozenset(
    [
        "a",
        "an",
        "the",
        "and",
        "or",
        "of",
        "in",
        "on",
        "for",
        "to",
        "with",
        "by",
        "from",
        "as",
        "at",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "that",
        "this",
        "these",
        "those",
        "it",
        "its",
        "we",
        "our",
        "their",
        "his",
        "her",
        "they",
        "them",
        "there",
        "here",
        "which",
        "who",
        "whom",
        "whose",
        "have",
        "has",
        "had",
        "do",
        "does",
        "did",
        "not",
        "no",
        "but",
        "if",
        "then",
        "than",
        "so",
        "such",
        "can",
        "could",
        "may",
        "might",
        "will",
        "would",
        "shall",
        "should",
        "must",
        "into",
        "over",
        "under",
        "between",
        "during",
        "about",
        "after",
        "before",
        "more",
        "most",
        "other",
        "some",
        "any",
        "all",
        "both",
        "each",
        "few",
        "many",
        "much",
        "own",
        "same",
        "very",
        "just",
        "also",
    ]
)


def _content_tokens(text: str) -> set[str]:
    return {t for t in _TOKEN_RE.findall(text.lower()) if len(t) > 3 and t not in _STOPWORDS}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _infer_claim_type(sentence: str, category: ClaimCategory) -> ClaimType:
    """Deterministic claim typing for offline mode.

    Mirrors the taxonomy the model is asked to apply, so the offline path
    exercises the same type-driven scoring rather than defaulting everything
    to one bucket.
    """
    lowered = sentence.lower()

    if lexicon.has_puffery(sentence) and not lexicon.find_quantities(sentence):
        return ClaimType.MARKETING
    if re.search(r"\b(we will|we plan|plans to|expects? to|on track to|by 20\d\d)\b", lowered):
        return ClaimType.FORWARD_LOOKING
    if re.search(r"\b(our (mission|vision|goal)|founded to|built to|committed to)\b", lowered):
        return ClaimType.CORPORATE_VISION
    if re.search(r"\$\s?\d|\bbillion\b|\bmarket (size|opportunity)\b|\btam\b", lowered):
        return ClaimType.MARKET_ESTIMATE
    if re.search(r"\b(approved|approval|licensed|cleared|authoris|authoriz)\b", lowered):
        return ClaimType.REGULATORY_APPROVAL
    if re.search(r"\b(filed|submitted|pdufa|bla|nda|maa|under review)\b", lowered):
        return ClaimType.REGULATORY_SUBMISSION
    # Checked before the phase rule: "our Phase 1 success rate is 62%" is a
    # claim about the company's history, not about where a programme sits.
    if re.search(
        r"\b(success rate|track record|probability of success|\bpos\b|versus industry|"
        r"vs\.? industry|industry (standard|average|benchmark))\b",
        lowered,
    ):
        return ClaimType.TRACK_RECORD
    if re.search(r"\bphase\s*(1|2|3|4|i|ii|iii|iv)\b|registrational", lowered):
        # A phase mention with a result is a result; without one it is a stage.
        if re.search(r"\b(readout|met|achieved|demonstrated|showed|orr|survival)\b", lowered):
            return ClaimType.CLINICAL_RESULT
        return ClaimType.PIPELINE_STAGE
    if re.search(r"\b(partnership|partnered|collaborat|licens(ed|ing) (to|with))\b", lowered):
        return ClaimType.PARTNERSHIP
    if re.search(
        r"\b(patent|intellectual property|composition of matter|freedom to operate)\b", lowered
    ):
        return ClaimType.IP_POSITION
    if re.search(r"\b(success rate|track record|probability of success|pos\b)\b", lowered):
        return ClaimType.TRACK_RECORD
    if re.search(r"\b(manufactur|cmc|gmp|cost of goods|scale-up|yield)\b", lowered):
        return ClaimType.MANUFACTURING
    if re.search(
        r"\b(first-in-class|best-in-class|only approved|versus competitor|unlike)\b", lowered
    ):
        return ClaimType.COMPETITIVE_POSITION
    if re.search(r"\b(platform|modular|our technology enables)\b", lowered):
        return ClaimType.PLATFORM_CAPABILITY

    return {
        ClaimCategory.CLINICAL_EFFICACY: ClaimType.CLINICAL_RESULT,
        ClaimCategory.PRECLINICAL_EFFICACY: ClaimType.PRECLINICAL_RESULT,
        ClaimCategory.MECHANISM: ClaimType.MECHANISM,
        ClaimCategory.SAFETY: ClaimType.SAFETY,
        ClaimCategory.BIOMARKER: ClaimType.BIOMARKER,
        ClaimCategory.PLATFORM: ClaimType.PLATFORM_CAPABILITY,
        ClaimCategory.REGULATORY: ClaimType.REGULATORY_SUBMISSION,
        ClaimCategory.IP: ClaimType.IP_POSITION,
        ClaimCategory.MANUFACTURING: ClaimType.MANUFACTURING,
        ClaimCategory.COMPETITIVE: ClaimType.COMPETITIVE_POSITION,
        ClaimCategory.MARKET: ClaimType.MARKET_ESTIMATE,
        ClaimCategory.TARGET_VALIDATION: ClaimType.MECHANISM,
    }.get(category, ClaimType.OTHER)


def _scientific_assessment(context: dict[str, Any], request: LLMRequest) -> dict[str, Any]:
    unavailable = (
        "Not assessed offline: this requires scientific reasoning over the retrieved "
        "evidence. " + STUB_NOTE
    )
    return {
        "biological_plausibility": unavailable,
        "plausibility_confidence": ConfidenceLevel.LOW.value,
        "modality_precedent": {
            "modality": str(context.get("modality") or "not identified"),
            "has_approved_precedent": False,
            "precedent_summary": unavailable,
            "notable_failures": [],
        },
        "first_in_class": unavailable,
        "differentiation": unavailable,
        "de_risking_achieved": unavailable,
        "partnerability": unavailable,
        "milestones_that_matter": [
            "Configure a language-model provider and re-run to generate this analysis."
        ],
        "key_failure_mode": unavailable,
    }


def _scorecard_commentary(context: dict[str, Any], request: LLMRequest) -> dict[str, Any]:
    dimensions = context.get("dimensions", []) or []
    score = context.get("overall_score", 0.0)
    return {
        "headline": (
            f"Composite scientific credibility is {score}/100. Narrative interpretation "
            "requires a language-model provider; the scores themselves are computed "
            "deterministically and are valid."
        ),
        "commentary": [
            {
                "dimension": str(d.get("dimension", "")),
                "so_what": STUB_NOTE,
                "what_would_change_it": (
                    "Configure a language-model provider and re-run for dimension analysis."
                ),
            }
            for d in dimensions
        ],
        "decisive_factors": [STUB_NOTE],
    }


_HANDLERS[S.ScientificAssessmentOut.__name__] = _scientific_assessment
_HANDLERS[S.ScorecardCommentaryOut.__name__] = _scorecard_commentary
