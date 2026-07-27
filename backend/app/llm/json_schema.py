"""Pydantic -> OpenAI *strict* JSON Schema conversion.

OpenAI structured outputs accept only a restricted subset of JSON Schema when
``strict: true``:

* every object must declare ``additionalProperties: false``;
* every property must be listed in ``required`` (optionality is expressed with
  a nullable ``anyOf``);
* validation keywords such as ``minimum``/``maxLength``/``format``/``default``
  are rejected;
* ``allOf`` is not supported.

Pydantic emits all of those.  Rather than hand-writing schemas (which drift
from the models that parse the response) we generate them and then normalise.
Dropped numeric/length constraints are folded into the field description so
the model still sees the requirement, and Pydantic re-validates the parsed
payload afterwards, so nothing is lost.
"""

from __future__ import annotations

import copy
from typing import Any

from pydantic import BaseModel

#: Keywords the strict schema dialect understands.
_ALLOWED_KEYS = {
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

#: Constraint keywords we translate into prose before dropping.
_CONSTRAINT_HINTS: dict[str, str] = {
    "minimum": ">= {v}",
    "maximum": "<= {v}",
    "exclusiveMinimum": "> {v}",
    "exclusiveMaximum": "< {v}",
    "minLength": "at least {v} characters",
    "maxLength": "at most {v} characters",
    "minItems": "at least {v} items",
    "maxItems": "at most {v} items",
    "pattern": "matching /{v}/",
    "format": "in {v} format",
}


def _merge_all_of(node: dict[str, Any]) -> dict[str, Any]:
    """Flatten ``allOf`` (pydantic uses it to attach metadata to a ``$ref``)."""
    parts = node.pop("allOf", None)
    if not parts:
        return node
    merged: dict[str, Any] = {}
    for part in parts:
        merged.update(part)
    # Sibling keys on the original node win (they are the annotations).
    for key, value in node.items():
        merged[key] = value
    return merged


def _describe_constraints(node: dict[str, Any]) -> str:
    hints = [
        template.format(v=node[key]) for key, template in _CONSTRAINT_HINTS.items() if key in node
    ]
    return f" ({', '.join(hints)})" if hints else ""


#: Keys whose value is a *map of schemas* rather than a schema.
_SCHEMA_MAP_KEYS = {"properties", "$defs", "definitions"}
#: Keys whose value is a *list of schemas*.
_SCHEMA_LIST_KEYS = {"anyOf", "oneOf"}


def _normalize(node: Any) -> Any:
    if not isinstance(node, dict):
        return node

    node = _merge_all_of(dict(node))

    hint = _describe_constraints(node)
    if hint:
        node["description"] = (node.get("description", "").rstrip() + hint).strip()

    cleaned: dict[str, Any] = {}
    for key, value in node.items():
        if key not in _ALLOWED_KEYS:
            continue
        if key in _SCHEMA_MAP_KEYS and isinstance(value, dict):
            cleaned[key] = {k: _normalize(v) for k, v in value.items()}
        elif key in _SCHEMA_LIST_KEYS and isinstance(value, list):
            cleaned[key] = [_normalize(v) for v in value]
        elif key in {"enum", "required"}:
            cleaned[key] = value
        elif key == "items":
            cleaned[key] = _normalize(value)
        else:
            cleaned[key] = _normalize(value) if isinstance(value, dict) else value

    if cleaned.get("properties") is not None:
        cleaned["type"] = cleaned.get("type", "object")
        cleaned["additionalProperties"] = False
        # Strict mode requires *all* properties to be required.
        cleaned["required"] = sorted(cleaned["properties"].keys())
    elif cleaned.get("type") == "object" and "$ref" not in cleaned:
        cleaned["properties"] = {}
        cleaned["required"] = []
        cleaned["additionalProperties"] = False

    return cleaned


def to_strict_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Return a strict-mode JSON Schema for ``model``."""
    raw = copy.deepcopy(model.model_json_schema(ref_template="#/$defs/{model}"))
    schema = _normalize(raw)
    if schema.get("type") != "object":
        raise ValueError(f"{model.__name__} must serialise to a JSON object")
    schema.pop("title", None)
    return schema


def schema_payload(name: str, model: type[BaseModel], *, strict: bool = True) -> dict[str, Any]:
    """Build the ``text.format`` payload for the OpenAI Responses API."""
    return {
        "type": "json_schema",
        "name": name,
        "strict": strict,
        "schema": to_strict_schema(model),
    }


def describe_schema_for_prompt(model: type[BaseModel]) -> str:
    """Compact human-readable field list, used by non-strict fallbacks."""
    schema = model.model_json_schema()
    lines: list[str] = []

    def walk(props: dict[str, Any], prefix: str = "") -> None:
        for name, spec in props.items():
            desc = spec.get("description", "")
            typ = spec.get("type") or ("enum" if "enum" in spec else "object")
            lines.append(f"- {prefix}{name} ({typ}): {desc}".rstrip())

    walk(schema.get("properties", {}))
    return "\n".join(lines)
