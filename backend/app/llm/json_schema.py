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


def _resolve_ref_siblings(node: dict[str, Any], defs: dict[str, Any]) -> dict[str, Any]:
    """Remove ``$ref`` nodes that carry sibling keywords.

    OpenAI's strict dialect rejects ``{"$ref": ..., "description": ...}`` with
    "$ref cannot have keywords". Pydantic emits exactly that whenever an enum
    field carries a ``Field(description=...)``, which is every enum field we
    define, so without this the whole schema is refused and every call silently
    degrades to non-strict decoding.

    Enums are inlined, which keeps the per-field description *and* satisfies
    the dialect. Object references cannot be inlined safely (they may recurse),
    so their sibling keywords are dropped and the bare ``$ref`` is kept.
    """
    ref = node.get("$ref")
    siblings = {k: v for k, v in node.items() if k != "$ref"}
    if not ref or not siblings:
        return node

    target = defs.get(ref.rsplit("/", 1)[-1], {})
    # A definition with no properties is a scalar/enum and is safe to inline.
    if target and "properties" not in target:
        inlined = {k: v for k, v in target.items() if k != "title"}
        # The field's own description is more specific than the type's.
        inlined.update(siblings)
        return inlined

    return {"$ref": ref}


def _describe_constraints(node: dict[str, Any]) -> str:
    hints = [
        template.format(v=node[key]) for key, template in _CONSTRAINT_HINTS.items() if key in node
    ]
    return f" ({', '.join(hints)})" if hints else ""


#: Keys whose value is a *map of schemas* rather than a schema.
_SCHEMA_MAP_KEYS = {"properties", "$defs", "definitions"}
#: Keys whose value is a *list of schemas*.
_SCHEMA_LIST_KEYS = {"anyOf", "oneOf"}


def _normalize(node: Any, defs: dict[str, Any] | None = None) -> Any:
    if not isinstance(node, dict):
        return node

    defs = defs if defs is not None else {}
    node = _merge_all_of(dict(node))
    node = _resolve_ref_siblings(node, defs)

    hint = _describe_constraints(node)
    if hint:
        node["description"] = (node.get("description", "").rstrip() + hint).strip()

    cleaned: dict[str, Any] = {}
    for key, value in node.items():
        if key not in _ALLOWED_KEYS:
            continue
        if key in _SCHEMA_MAP_KEYS and isinstance(value, dict):
            cleaned[key] = {k: _normalize(v, defs) for k, v in value.items()}
        elif key in _SCHEMA_LIST_KEYS and isinstance(value, list):
            cleaned[key] = [_normalize(v, defs) for v in value]
        elif key in {"enum", "required"}:
            cleaned[key] = value
        elif key == "items":
            cleaned[key] = _normalize(value, defs)
        else:
            cleaned[key] = _normalize(value, defs) if isinstance(value, dict) else value

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
    schema = _normalize(raw, raw.get("$defs", {}))
    schema = _prune_unused_defs(schema)
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


def _prune_unused_defs(schema: dict[str, Any]) -> dict[str, Any]:
    """Drop ``$defs`` entries no longer referenced after enum inlining.

    Strict mode rejects a schema containing definitions nothing points at.
    """
    defs = schema.get("$defs")
    if not defs:
        return schema

    referenced: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            ref = node.get("$ref")
            if isinstance(ref, str):
                referenced.add(ref.rsplit("/", 1)[-1])
            for key, value in node.items():
                if key != "$defs":
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk({k: v for k, v in schema.items() if k != "$defs"})

    # A retained definition may itself reference others; close over them.
    changed = True
    while changed:
        changed = False
        for name in list(referenced):
            target = defs.get(name)
            if target is None:
                continue
            before = len(referenced)
            walk(target)
            if len(referenced) != before:
                changed = True

    kept = {name: body for name, body in defs.items() if name in referenced}
    if kept:
        schema["$defs"] = kept
    else:
        schema.pop("$defs", None)
    return schema
