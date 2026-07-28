"""LLM layer: strict schema generation, validation, repair, budget, prompts."""

from __future__ import annotations

import json

import pytest
from pydantic import BaseModel, Field

from app.core.errors import LLMBudgetExceeded, LLMError, LLMSchemaError
from app.llm import prompts
from app.llm import schemas as S
from app.llm.base import LLMRequest, LLMResponse, Usage
from app.llm.client import LLMClient, _extract_json
from app.llm.json_schema import to_strict_schema
from app.llm.pricing import estimate_cost_usd, price_for
from app.llm.stub_provider import StubProvider

STRICT_KEYS = {
    "type",
    "properties",
    "required",
    "additionalProperties",
    "items",
    "enum",
    "const",
    "anyOf",
    "$ref",
    "$defs",
    "description",
    "title",
}

ALL_SCHEMAS = [
    S.ScientificAssessmentOut,
    S.ScorecardCommentaryOut,
    S.PageUnderstandingOut,
    S.CompanyProfileOut,
    S.EntityExtractionOut,
    S.ClaimExtractionOut,
    S.QueryPlanOut,
    S.AdjudicationOut,
    S.BatchAdjudicationOut,
    S.ClaimVerdictOut,
    S.RisksAndQuestionsOut,
    S.ReportOut,
]


def _walk(node, path="root"):
    """Yield every schema object in the tree with its path."""
    if isinstance(node, dict):
        yield path, node
        for key, value in node.items():
            if key in {"properties", "$defs"} and isinstance(value, dict):
                for name, child in value.items():
                    yield from _walk(child, f"{path}.{key}.{name}")
            elif key == "anyOf" and isinstance(value, list):
                for index, child in enumerate(value):
                    yield from _walk(child, f"{path}.anyOf[{index}]")
            elif key == "items":
                yield from _walk(value, f"{path}.items")


class TestStrictSchema:
    @pytest.mark.parametrize("model", ALL_SCHEMAS, ids=lambda m: m.__name__)
    def test_objects_forbid_extra_properties(self, model):
        for path, node in _walk(to_strict_schema(model)):
            if "properties" in node:
                assert node.get("additionalProperties") is False, path

    @pytest.mark.parametrize("model", ALL_SCHEMAS, ids=lambda m: m.__name__)
    def test_every_property_is_required(self, model):
        for path, node in _walk(to_strict_schema(model)):
            if "properties" in node:
                assert set(node.get("required", [])) == set(node["properties"]), path

    @pytest.mark.parametrize("model", ALL_SCHEMAS, ids=lambda m: m.__name__)
    def test_only_supported_keywords_survive(self, model):
        for path, node in _walk(to_strict_schema(model)):
            unsupported = set(node) - STRICT_KEYS
            assert not unsupported, f"{path}: {unsupported}"

    def test_constraints_move_into_descriptions(self):
        schema = to_strict_schema(S.ClaimVerdictOut)
        description = schema["properties"]["novelty"]["description"]
        assert ">= 0.0" in description and "<= 1.0" in description

    def test_optional_fields_become_nullable(self):
        schema = to_strict_schema(S.ClaimVerdictOut)
        any_of = schema["properties"]["translational_gap"]["anyOf"]
        assert {"type": "null"} in any_of

    def test_nested_models_are_referenced(self):
        schema = to_strict_schema(S.ClaimExtractionOut)
        assert "$defs" in schema
        assert "ExtractedClaim" in schema["$defs"]

    def test_enums_are_inlined_with_their_field_description(self):
        """OpenAI strict mode rejects ``$ref`` carrying sibling keywords.

        Pydantic emits exactly that for every enum field with a description,
        so enums are inlined -- keeping the per-field guidance and satisfying
        the dialect. Before this, every call silently fell back to non-strict.
        """
        schema = to_strict_schema(S.ClaimExtractionOut)
        claim_type = schema["$defs"]["ExtractedClaim"]["properties"]["claim_type"]
        assert claim_type["enum"], "enum values must survive inlining"
        assert claim_type["description"], "the field description must survive"
        assert "$ref" not in claim_type

    def test_no_ref_carries_sibling_keywords(self):
        """The invariant OpenAI enforces; asserted over every contract."""

        def walk(node, path="root"):
            if isinstance(node, dict):
                if "$ref" in node and len(node) > 1:
                    raise AssertionError(f"{path}: $ref with siblings {set(node) - {'$ref'}}")
                for key, value in node.items():
                    if key in {"properties", "$defs"} and isinstance(value, dict):
                        for name, child in value.items():
                            walk(child, f"{path}.{key}.{name}")
                    elif key == "anyOf" and isinstance(value, list):
                        for index, child in enumerate(value):
                            walk(child, f"{path}.anyOf[{index}]")
                    elif key == "items":
                        walk(value, f"{path}.items")
                    elif isinstance(value, dict):
                        walk(value, f"{path}.{key}")

        for model in ALL_SCHEMAS:
            walk(to_strict_schema(model), model.__name__)

    def test_unused_definitions_are_pruned(self):
        """Strict mode rejects a schema with definitions nothing references."""
        schema = to_strict_schema(S.ClaimExtractionOut)
        referenced: set[str] = set()

        def collect(node):
            if isinstance(node, dict):
                ref = node.get("$ref")
                if isinstance(ref, str):
                    referenced.add(ref.rsplit("/", 1)[-1])
                for key, value in node.items():
                    if key != "$defs":
                        collect(value)
            elif isinstance(node, list):
                for item in node:
                    collect(item)

        collect({k: v for k, v in schema.items() if k != "$defs"})
        for name in schema.get("$defs", {}):
            collect(schema["$defs"][name])
        assert set(schema.get("$defs", {})) <= referenced


class TestPrompts:
    ALL_PROMPTS = {
        "system_base": {},
        "page_understanding": {"page_number": 1, "text_layer": "x", "tables": "y"},
        "claims": {"pages": "x"},
        "entities": {"pages": "x"},
        "company_profile": {"pages": "x"},
        "query_plan": {
            "claim_statement": "s",
            "claim_category": "c",
            "claimed_tier": "t",
            "entity_names": "e",
            "company_context": "ctx",
        },
        "adjudication": {
            "claim_statement": "s",
            "claim_quote": "q",
            "claim_category": "c",
            "claim_type": "mechanism",
            "corroboration_guidance": "g",
            "claimed_tier": "t",
            "evidence": "e",
        },
        "claim_verdict": {
            "claim_statement": "s",
            "claim_quote": "q",
            "claimed_tier": "t",
            "claim_category": "c",
            "claim_type": "mechanism",
            "corroboration_guidance": "g",
            "verification_summary": "v",
            "supporting_count": 1,
            "contradicting_count": 0,
            "neutral_count": 0,
            "evidence_count": 1,
            "evidence_summary": "e",
        },
        "risks_questions": {"company_context": "c", "claims": "x", "signals": "y"},
        "scientific_assessment": {
            "company_context": "c",
            "thesis_claims": "t",
            "evidence_digest": "e",
            "competitive_records": "r",
        },
        "scorecard": {"scorecard": "s", "drivers": "d"},
        "report": {
            "company_context": "c",
            "scorecard": "s",
            "scientific_assessment": "a",
            "claims": "x",
            "risks": "r",
            "questions": "q",
            "section_plan": "p",
            "evidence_ledger": "l",
            "section_claims": "sc",
        },
    }

    def test_every_prompt_file_is_covered(self):
        """A new prompt without a render test is a runtime failure waiting."""
        from app.llm.prompts import PROMPT_DIR

        on_disk = {path.stem for path in PROMPT_DIR.glob("*.md")}
        assert on_disk == set(self.ALL_PROMPTS)

    @pytest.mark.parametrize("name", sorted(ALL_PROMPTS))
    def test_every_prompt_renders(self, name):
        """Catches unescaped '$' in prompt text, which fails only at runtime."""
        import re

        rendered = prompts.render(name, **self.ALL_PROMPTS[name])
        assert len(rendered) > 100
        leftover = re.findall(r"\$\{?[A-Za-z_][A-Za-z0-9_]*\}?", rendered)
        assert not leftover, f"unsubstituted placeholders in {name}: {leftover}"

    def test_system_prompt_states_the_core_rules(self):
        system = prompts.system()
        for rule in ("Never invent facts", "Never invent citations", "Quotes are literal"):
            assert rule in system

    def test_missing_placeholder_raises(self):
        with pytest.raises(KeyError):
            prompts.render("claims")


class TestClient:
    async def test_returns_validated_model(self, llm):
        result = await llm.structured(
            purpose="claims",
            system="s",
            user="u",
            schema=S.ClaimExtractionOut,
            context={
                "pages": [
                    {"page_number": 1, "text": "NG-101 inhibits LRRK2 with an IC50 of 3.2 nM."}
                ]
            },
        )
        assert isinstance(result, S.ClaimExtractionOut)
        assert result.claims

    async def test_budget_is_enforced(self):
        client = LLMClient(StubProvider(), max_calls=1, persist_logs=False)
        await client.structured(
            purpose="p", system="s", user="u", schema=S.ClaimExtractionOut, context={"pages": []}
        )
        with pytest.raises(LLMBudgetExceeded):
            await client.structured(
                purpose="p",
                system="s",
                user="u",
                schema=S.ClaimExtractionOut,
                context={"pages": []},
            )

    async def test_metrics_are_accumulated(self, llm):
        await llm.structured(
            purpose="entities",
            system="s",
            user="u",
            schema=S.EntityExtractionOut,
            context={"pages": []},
        )
        metrics = llm.metrics.to_dict()
        assert metrics["calls"] == 1
        assert metrics["by_purpose"]["entities"] == 1

    async def test_schema_violation_triggers_one_repair(self):
        class Broken:
            name = "broken"
            calls = 0

            async def complete_structured(self, request: LLMRequest) -> LLMResponse:
                Broken.calls += 1
                text = (
                    '{"nope": 1}'
                    if Broken.calls == 1
                    else json.dumps(
                        {
                            "corroboration_status": "insufficient_evidence",
                            "confidence": "low",
                            "confidence_reason": "nothing on point was retrieved",
                            "verdict": "v",
                            "comparisons": [],
                            "key_uncertainties": [],
                            "novelty": 0.5,
                            "translational_gap": None,
                            "so_what": "s",
                        }
                    )
                )
                return LLMResponse(
                    text=text, model="m", usage=Usage(), latency_ms=1, provider="broken"
                )

            async def embed(self, texts, *, model, dimensions):
                return []

            async def aclose(self):
                return None

        client = LLMClient(Broken(), persist_logs=False)
        result = await client.structured(
            purpose="verdict", system="s", user="u", schema=S.ClaimVerdictOut
        )
        assert result.verdict == "v"
        assert Broken.calls == 2
        assert client.metrics.repairs == 1

    async def test_persistent_schema_violation_raises(self):
        class AlwaysBad:
            name = "bad"

            async def complete_structured(self, request):
                return LLMResponse(
                    text="{}", model="m", usage=Usage(), latency_ms=1, provider="bad"
                )

            async def embed(self, texts, *, model, dimensions):
                return []

            async def aclose(self):
                return None

        client = LLMClient(AlwaysBad(), persist_logs=False)
        with pytest.raises(LLMSchemaError):
            await client.structured(
                purpose="verdict", system="s", user="u", schema=S.ClaimVerdictOut
            )

    async def test_provider_failure_is_wrapped(self):
        class Exploding:
            name = "boom"

            async def complete_structured(self, request):
                raise RuntimeError("connection reset")

            async def embed(self, texts, *, model, dimensions):
                return []

            async def aclose(self):
                return None

        client = LLMClient(Exploding(), persist_logs=False)
        with pytest.raises(LLMError):
            await client.structured(purpose="p", system="s", user="u", schema=S.ClaimVerdictOut)
        assert client.metrics.failed_calls == 1

    def test_degraded_flag_reflects_provider(self, llm):
        assert llm.is_degraded is True


class TestJsonExtraction:
    def test_plain_json(self):
        assert _extract_json('{"a": 1}') == {"a": 1}

    def test_code_fenced_json(self):
        assert _extract_json('```json\n{"a": 1}\n```') == {"a": 1}

    def test_json_with_leading_prose(self):
        assert _extract_json('Here you go:\n{"a": 1}') == {"a": 1}

    def test_non_json_raises(self):
        with pytest.raises(LLMSchemaError):
            _extract_json("no json here at all")


class TestStubProvider:
    @pytest.mark.parametrize("model", ALL_SCHEMAS, ids=lambda m: m.__name__)
    async def test_handles_every_schema(self, model):
        """The offline provider must satisfy every contract, or a run half-fails."""
        provider = StubProvider()
        response = await provider.complete_structured(
            LLMRequest(
                purpose="t",
                system="s",
                user="u",
                schema_name=model.__name__,
                schema_model=model,
                model="stub",
                context={
                    "pages": [{"page_number": 1, "text": "LRRK2 inhibitor with IC50 of 3 nM."}],
                    "page_text": "LRRK2 inhibitor",
                    "page_number": 1,
                    "evidence": [{"ref": "E1", "title": "t", "abstract": "a"}],
                    "claims": [{"ref": "C1", "statement": "s", "importance": 0.9}],
                    "section_plan": [{"heading": "H", "instruction": "i"}],
                },
            )
        )
        model.model_validate(json.loads(response.text))

    async def test_embeddings_are_deterministic_and_normalised(self):
        provider = StubProvider()
        first = await provider.embed(["LRRK2 kinase inhibitor"], model="m", dimensions=64)
        second = await provider.embed(["LRRK2 kinase inhibitor"], model="m", dimensions=64)
        assert first == second
        norm = sum(v * v for v in first[0]) ** 0.5
        assert norm == pytest.approx(1.0, abs=1e-6)

    async def test_similar_text_scores_higher_than_unrelated(self):
        from app.evidence.retriever import cosine

        provider = StubProvider()
        vectors = await provider.embed(
            [
                "LRRK2 kinase inhibitor for Parkinson disease",
                "LRRK2 kinase inhibition in Parkinson disease patients",
                "supply chain logistics for retail distribution",
            ],
            model="m",
            dimensions=256,
        )
        assert cosine(vectors[0], vectors[1]) > cosine(vectors[0], vectors[2])


class TestPricing:
    def test_dated_snapshots_inherit_prices(self):
        assert price_for("gpt-5-mini-2025-08-07") == price_for("gpt-5-mini")

    def test_longest_prefix_wins(self):
        assert price_for("gpt-5-mini").input < price_for("gpt-5").input

    def test_cost_uses_cached_input_rate(self):
        fresh = estimate_cost_usd("gpt-5", Usage(input_tokens=1_000_000, output_tokens=0))
        cached = estimate_cost_usd(
            "gpt-5", Usage(input_tokens=1_000_000, cached_input_tokens=1_000_000)
        )
        assert cached < fresh

    def test_unknown_model_costs_zero(self):
        assert estimate_cost_usd("some-unknown-model", Usage(input_tokens=1000)) == 0.0


class TestSchemaNames:
    def test_schema_name_is_api_safe(self):
        from app.llm.client import _schema_name

        class Weird(BaseModel):
            x: int = Field(description="d")

        assert _schema_name(Weird) == "Weird"
