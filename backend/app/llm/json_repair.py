"""Recovery of JSON that a model stopped emitting half-way through.

When a structured-output call exhausts its output-token budget the provider
returns a syntactically incomplete document -- typically a list whose final
element is cut mid-string.  ``json.loads`` reports something like
``Expecting ',' delimiter: line 1 column 13324``, and the naive
"find the first ``{`` and the last ``}``" fallback makes it worse, because the
last ``}`` in a truncated document belongs to a nested object rather than to
the root.

Dropping the whole response in that situation throws away every complete
element the model *did* produce.  This module instead rewinds to the last
element that closed cleanly and balances the still-open containers, so a
truncated extraction degrades to a partial one.

Salvage is a safety net, never a strategy: the caller is expected to log it
loudly and to send smaller requests so it stops happening.
"""

from __future__ import annotations

import json
from typing import Any

__all__ = ["close_truncated_json", "salvage_json"]

_CLOSERS = {"{": "}", "[": "]"}


def _scan(text: str) -> tuple[list[str], int | None]:
    """Return the container stack at end of ``text`` and the last safe cut.

    The safe cut is the offset just past the most recent *nested* container
    that closed, i.e. the end of the last fully-formed element of an
    incomplete list or object.
    """
    stack: list[str] = []
    last_element_end: int | None = None
    in_string = False
    escaped = False

    for index, char in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
        elif char in _CLOSERS:
            stack.append(char)
        elif char in ("}", "]"):
            if stack:
                stack.pop()
            if stack:
                # A nested container closed while the root is still open:
                # everything up to here is a complete element.
                last_element_end = index + 1

    return stack, last_element_end


def close_truncated_json(text: str) -> str | None:
    """Rewind ``text`` to its last complete element and close open containers.

    Returns ``None`` when the text is not recoverable -- it is already
    balanced, it never opened a container, or nothing completed before the
    truncation point.
    """
    if not text:
        return None

    stack, last_element_end = _scan(text)
    if not stack:
        return None  # balanced already; the failure was not truncation
    if last_element_end is None:
        return None  # nothing completed before the cut

    head = text[:last_element_end]
    open_containers, _ = _scan(head)
    return head + "".join(_CLOSERS[char] for char in reversed(open_containers))


def salvage_json(text: str) -> Any | None:
    """Parse ``text`` as JSON, repairing a truncated document if necessary."""
    if not text:
        return None
    candidate = text.strip()
    start = candidate.find("{")
    if start < 0:
        start = candidate.find("[")
    if start < 0:
        return None
    candidate = candidate[start:]

    repaired = close_truncated_json(candidate)
    if repaired is None:
        return None
    try:
        return json.loads(repaired)
    except json.JSONDecodeError:
        return None
