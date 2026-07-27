"""Stages 3-4: company profile and scientific entity extraction.

Entities are extracted by the model and then reconciled with deterministic
gazetteer hits (:mod:`app.extraction.lexicon`).  The reconciliation is
asymmetric on purpose:

* a model entity confirmed by the gazetteer gains confidence;
* a gazetteer hit the model missed is added as a low-confidence candidate,
  because silently dropping a target that is plainly written in the deck is
  the worse failure;
* a model entity the gazetteer cannot see is kept as-is -- the gazetteer's
  coverage is deliberately narrow.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.core.config import settings
from app.core.enums import EntityType
from app.core.logging import get_logger
from app.extraction import lexicon
from app.llm import prompts
from app.llm.client import LLMClient
from app.llm.schemas import CompanyProfileOut, EntityExtractionOut, ExtractedEntity
from app.utils.chunking import render_pages_for_prompt
from app.utils.text import normalize_entity_key, truncate

log = get_logger(__name__)

#: Confidence assigned to a gazetteer hit the model did not report.
BACKSTOP_CONFIDENCE = 0.35
#: Confidence bonus when model and gazetteer agree.
CORROBORATION_BONUS = 0.12


@dataclass(slots=True)
class ResolvedEntity:
    entity_type: EntityType
    name: str
    normalized_key: str
    canonical_name: str | None = None
    aliases: list[str] = field(default_factory=list)
    description: str | None = None
    role_in_program: str | None = None
    source_pages: list[int] = field(default_factory=list)
    confidence: float = 0.0
    mention_count: int = 0
    salience: float = 0.0
    #: True when only the deterministic gazetteer found this entity.
    from_backstop: bool = False

    def merge(self, other: ResolvedEntity) -> None:
        self.confidence = max(self.confidence, other.confidence)
        self.canonical_name = self.canonical_name or other.canonical_name
        self.description = self.description or other.description
        self.role_in_program = self.role_in_program or other.role_in_program
        for alias in [other.name, *other.aliases]:
            if alias.lower() != self.name.lower() and alias not in self.aliases:
                self.aliases.append(alias)
        for page in other.source_pages:
            if page not in self.source_pages:
                self.source_pages.append(page)
        self.mention_count += other.mention_count
        self.from_backstop = self.from_backstop and other.from_backstop


@dataclass(slots=True)
class EntityExtractionResult:
    entities: list[ResolvedEntity] = field(default_factory=list)
    model_count: int = 0
    backstop_added: int = 0

    def metrics(self) -> dict[str, Any]:
        by_type: dict[str, int] = {}
        for entity in self.entities:
            by_type[entity.entity_type.value] = by_type.get(entity.entity_type.value, 0) + 1
        return {
            "entities": len(self.entities),
            "from_model": self.model_count,
            "added_by_backstop": self.backstop_added,
            "by_type": by_type,
        }

    def by_name(self) -> dict[str, ResolvedEntity]:
        """Lookup keyed by every surface form, for linking claims to entities."""
        index: dict[str, ResolvedEntity] = {}
        for entity in self.entities:
            for surface in [entity.name, entity.canonical_name or "", *entity.aliases]:
                if surface:
                    index.setdefault(normalize_entity_key(surface), entity)
        return index


class EntityExtractionStage:
    def __init__(self, llm: LLMClient) -> None:
        self.llm = llm

    async def run(self, pages: list[dict[str, Any]]) -> EntityExtractionResult:
        usable = [p for p in pages if (p.get("text") or "").strip()]
        if not usable:
            return EntityExtractionResult()

        output = await self.llm.structured(
            purpose="entities",
            stage="entities",
            system=prompts.system(),
            user=prompts.render(
                "entities", pages=render_pages_for_prompt(usable, per_page_limit=4000)
            ),
            schema=EntityExtractionOut,
            model=settings.model_reasoning,
            context={"pages": usable},
        )

        resolved: dict[tuple[EntityType, str], ResolvedEntity] = {}
        for extracted in output.entities:
            entity = _from_model(extracted)
            key = (entity.entity_type, entity.normalized_key)
            if key in resolved:
                resolved[key].merge(entity)
            else:
                resolved[key] = entity
        model_count = len(resolved)

        backstop_added = self._apply_backstop(resolved, usable)
        entities = list(resolved.values())
        self._score_salience(entities, usable)
        entities.sort(key=lambda e: e.salience, reverse=True)

        result = EntityExtractionResult(
            entities=entities, model_count=model_count, backstop_added=backstop_added
        )
        log.info("entities.completed", **result.metrics())
        return result

    def _apply_backstop(
        self,
        resolved: dict[tuple[EntityType, str], ResolvedEntity],
        pages: list[dict[str, Any]],
    ) -> int:
        added = 0
        for page in pages:
            page_number = int(page.get("page_number", 0))
            for hit in lexicon.find_all_entities(page.get("text") or ""):
                key = (hit.entity_type, normalize_entity_key(hit.canonical))
                existing = resolved.get(key) or _find_by_alias(resolved, hit)
                if existing is not None:
                    existing.confidence = min(1.0, existing.confidence + CORROBORATION_BONUS)
                    if page_number and page_number not in existing.source_pages:
                        existing.source_pages.append(page_number)
                    continue
                resolved[key] = ResolvedEntity(
                    entity_type=hit.entity_type,
                    name=hit.canonical,
                    normalized_key=key[1],
                    canonical_name=hit.canonical,
                    aliases=[hit.surface] if hit.surface.lower() != hit.canonical.lower() else [],
                    source_pages=[page_number] if page_number else [],
                    confidence=BACKSTOP_CONFIDENCE,
                    mention_count=1,
                    from_backstop=True,
                )
                added += 1
        return added

    def _score_salience(self, entities: list[ResolvedEntity], pages: list[dict[str, Any]]) -> None:
        """Salience = how much of the deck is actually about this entity."""
        corpus = "\n".join((p.get("text") or "") for p in pages).lower()
        page_count = max(1, len(pages))
        for entity in entities:
            surfaces = {entity.name.lower(), (entity.canonical_name or "").lower()}
            surfaces.update(a.lower() for a in entity.aliases)
            mentions = sum(corpus.count(s) for s in surfaces if len(s) >= 3)
            entity.mention_count = max(entity.mention_count, mentions)
            page_spread = len(entity.source_pages) / page_count
            frequency = min(1.0, mentions / 12.0)
            type_weight = _TYPE_SALIENCE.get(entity.entity_type, 0.5)
            entity.salience = round(
                min(
                    1.0,
                    (0.45 * frequency + 0.35 * page_spread + 0.20 * type_weight)
                    * (0.6 + 0.4 * entity.confidence),
                ),
                4,
            )


#: Not all entity types matter equally to a scientific thesis.
_TYPE_SALIENCE = {
    EntityType.TARGET: 1.0,
    EntityType.DISEASE: 0.95,
    EntityType.DRUG: 0.9,
    EntityType.MECHANISM: 0.85,
    EntityType.MODALITY: 0.8,
    EntityType.BIOMARKER: 0.7,
    EntityType.ENDPOINT: 0.65,
    EntityType.PATHWAY: 0.6,
    EntityType.MODEL_SYSTEM: 0.5,
    EntityType.ASSAY: 0.4,
    EntityType.COMPANY: 0.3,
    EntityType.INSTITUTION: 0.2,
}


def _from_model(extracted: ExtractedEntity) -> ResolvedEntity:
    return ResolvedEntity(
        entity_type=extracted.entity_type,
        name=extracted.name,
        normalized_key=normalize_entity_key(extracted.canonical_name or extracted.name),
        canonical_name=extracted.canonical_name,
        aliases=list(extracted.aliases),
        description=extracted.description,
        role_in_program=extracted.role_in_program,
        source_pages=list(extracted.source_pages),
        confidence=extracted.confidence,
        mention_count=1,
    )


def _find_by_alias(
    resolved: dict[tuple[EntityType, str], ResolvedEntity], hit: lexicon.LexiconHit
) -> ResolvedEntity | None:
    surface_key = normalize_entity_key(hit.surface)
    for (entity_type, _), entity in resolved.items():
        if entity_type is not hit.entity_type:
            continue
        candidates = {normalize_entity_key(a) for a in entity.aliases}
        candidates.add(entity.normalized_key)
        if surface_key in candidates:
            return entity
    return None


# ------------------------------------------------------------------ profile ---
class CompanyProfileStage:
    def __init__(self, llm: LLMClient) -> None:
        self.llm = llm

    async def run(self, pages: list[dict[str, Any]]) -> CompanyProfileOut:
        usable = [p for p in pages if (p.get("text") or "").strip()]
        if not usable:
            return CompanyProfileOut.model_construct(
                company_name=None,
                one_liner=None,
                founded_year=None,
                headquarters=None,
                company_stage=None,
                lead_program=None,
                lead_indication=None,
                modality=None,
                development_stage=None,
                pipeline=[],
                team=[],
                total_raised=None,
                current_raise=None,
                use_of_funds=None,
                partnerships=[],
                ip_position=None,
                business_model=None,
                source_pages=[],
            )

        # The profile lives in the first and last slides (title, pipeline, team,
        # ask); sampling them keeps this call cheap without losing coverage.
        selected = _profile_pages(usable)
        document_text = "\n\n".join((p.get("text") or "") for p in usable)

        return await self.llm.structured(
            purpose="company_profile",
            stage="profile",
            system=prompts.system(),
            user=prompts.render(
                "company_profile", pages=render_pages_for_prompt(selected, per_page_limit=5000)
            ),
            schema=CompanyProfileOut,
            model=settings.model_fast,
            context={
                "document_text": truncate(document_text, 40_000),
                "first_page_text": usable[0].get("text", ""),
                "page_numbers": [int(p["page_number"]) for p in usable],
            },
        )


def _profile_pages(
    pages: list[dict[str, Any]], *, head: int = 6, tail: int = 5
) -> list[dict[str, Any]]:
    if len(pages) <= head + tail:
        return pages
    selected = pages[:head] + pages[-tail:]
    # Keep any middle page that looks like a pipeline or team slide.
    keywords = ("pipeline", "team", "leadership", "founders", "milestones", "financing", "the ask")
    for page in pages[head:-tail]:
        haystack = ((page.get("slide_title") or "") + " " + (page.get("text") or "")[:400]).lower()
        if any(keyword in haystack for keyword in keywords):
            selected.append(page)
    return sorted(selected, key=lambda p: int(p.get("page_number", 0)))
