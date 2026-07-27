"""Recovery of JSON cut off by the output-token limit.

The BioNTech run failed with ``Expecting ',' delimiter: line 1 column 13324``
because the model's entity list was longer than the 16,000-token output
budget.  The response was not malformed -- it was *incomplete*, and every
entity before the cut was perfectly good.  These tests pin the distinction.
"""

from __future__ import annotations

import json

import pytest

from app.core.errors import LLMSchemaError, LLMTruncatedError
from app.llm.base import LLMRequest, LLMResponse, Usage
from app.llm.client import LLMClient, estimated_input_tokens
from app.llm.json_repair import close_truncated_json, salvage_json
from app.llm.schemas import EntityExtractionOut


def _entity(name: str) -> dict:
    return {
        "entity_type": "target",
        "name": name,
        "canonical_name": name,
        "aliases": [],
        "description": None,
        "role_in_program": None,
        "source_pages": [1],
        "confidence": 0.8,
    }


COMPLETE = json.dumps({"entities": [_entity("KRAS"), _entity("LRRK2"), _entity("HER2")]})


class TestCloseTruncatedJson:
    def test_balanced_json_is_left_alone(self):
        assert close_truncated_json(COMPLETE) is None

    def test_a_list_cut_mid_object_keeps_the_complete_elements(self):
        cut = COMPLETE[: COMPLETE.index('"HER2"') + 3]
        payload = json.loads(close_truncated_json(cut))
        assert [e["name"] for e in payload["entities"]] == ["KRAS", "LRRK2"]

    def test_a_cut_inside_a_string_does_not_confuse_the_scanner(self):
        raw = json.dumps({"entities": [_entity("KRAS"), _entity('a "quoted" name')]})
        cut = raw[: raw.index("quoted")]
        payload = json.loads(close_truncated_json(cut))
        assert [e["name"] for e in payload["entities"]] == ["KRAS"]

    def test_a_cut_before_any_element_completes_is_unrecoverable(self):
        assert close_truncated_json('{"entities": [{"name": "KR') is None

    def test_empty_input_is_unrecoverable(self):
        assert close_truncated_json("") is None


class TestSalvageJson:
    def test_leading_prose_is_skipped(self):
        cut = COMPLETE[: COMPLETE.index('"HER2"')]
        assert salvage_json("Here you go:\n" + cut)["entities"]

    def test_text_without_json_yields_nothing(self):
        assert salvage_json("the model apologises and returns nothing") is None

    def test_salvaged_payload_validates_against_the_schema(self):
        cut = COMPLETE[: COMPLETE.index('"HER2"') + 3]
        model = EntityExtractionOut.model_validate(salvage_json(cut))
        assert len(model.entities) == 2


class _Provider:
    name = "test"

    def __init__(self, text: str, *, truncated: bool) -> None:
        self.text = text
        self.truncated = truncated
        self.calls = 0

    async def complete_structured(self, request: LLMRequest) -> LLMResponse:
        self.calls += 1
        return LLMResponse(
            text=self.text,
            model="m",
            usage=Usage(input_tokens=100, output_tokens=16_000),
            latency_ms=1,
            provider=self.name,
            truncated=self.truncated,
        )

    async def embed(self, texts, *, model, dimensions):
        return []

    async def aclose(self):
        return None


class TestClientHandlesTruncation:
    async def test_partial_entities_are_returned_rather_than_lost(self):
        cut = COMPLETE[: COMPLETE.index('"HER2"') + 3]
        provider = _Provider(cut, truncated=True)
        client = LLMClient(provider, persist_logs=False)

        result = await client.structured(
            purpose="entities", system="s", user="u", schema=EntityExtractionOut
        )
        assert [e.name for e in result.entities] == ["KRAS", "LRRK2"]
        assert client.metrics.salvaged == 1
        assert client.metrics.truncated == 1

    async def test_truncation_does_not_cost_a_repair_round_trip(self):
        """Resending the same prompt truncates at the same place; don't."""
        cut = COMPLETE[: COMPLETE.index('"HER2"') + 3]
        provider = _Provider(cut, truncated=True)
        client = LLMClient(provider, persist_logs=False)

        await client.structured(
            purpose="entities", system="s", user="u", schema=EntityExtractionOut
        )
        assert provider.calls == 1
        assert client.metrics.repairs == 0

    async def test_unsalvageable_truncation_asks_the_caller_to_split(self):
        provider = _Provider('{"entities": [{"name": "KR', truncated=True)
        client = LLMClient(provider, persist_logs=False)

        with pytest.raises(LLMTruncatedError):
            await client.structured(
                purpose="entities", system="s", user="u", schema=EntityExtractionOut
            )
        assert provider.calls == 1, "a truncated call must not be retried verbatim"

    async def test_a_genuinely_malformed_response_still_repairs(self):
        """Salvage must not swallow the ordinary schema-violation path."""
        provider = _Provider('{"wrong_key": []}', truncated=False)
        client = LLMClient(provider, persist_logs=False)

        with pytest.raises(LLMSchemaError):
            await client.structured(
                purpose="entities", system="s", user="u", schema=EntityExtractionOut
            )
        assert provider.calls == 2, "the repair round-trip should still happen"
        assert client.metrics.repairs == 1


class TestInputBudget:
    def test_estimate_counts_prompt_and_images(self):
        request = LLMRequest(
            purpose="p",
            system="a" * 400,
            user="b" * 4_000,
            schema_name="EntityExtractionOut",
            schema_model=EntityExtractionOut,
            model="m",
        )
        assert estimated_input_tokens(request) == pytest.approx(1_100, rel=0.05)

    async def test_oversized_prompt_is_counted_but_allowed_by_default(self, settings, monkeypatch):
        monkeypatch.setattr(settings, "llm_max_input_tokens", 100)
        provider = _Provider(COMPLETE, truncated=False)
        client = LLMClient(provider, persist_logs=False)

        await client.structured(
            purpose="entities", system="s", user="x" * 10_000, schema=EntityExtractionOut
        )
        assert client.metrics.over_input_budget == 1
        assert provider.calls == 1

    async def test_extraction_stages_treat_an_oversized_prompt_as_an_error(
        self, settings, monkeypatch
    ):
        from app.core.errors import LLMInputTooLarge

        monkeypatch.setattr(settings, "llm_max_input_tokens", 100)
        provider = _Provider(COMPLETE, truncated=False)
        client = LLMClient(provider, persist_logs=False)

        with pytest.raises(LLMInputTooLarge):
            await client.structured(
                purpose="entities",
                system="s",
                user="x" * 10_000,
                schema=EntityExtractionOut,
                enforce_input_budget=True,
            )
        assert provider.calls == 0, "the oversized call must never reach the provider"
