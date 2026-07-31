"""Stripping NUL bytes from values on their way into the database.

PostgreSQL ``text`` cannot store ``U+0000``: psycopg raises

    DataError: PostgreSQL text fields cannot contain NUL (0x00) bytes

and ``jsonb`` refuses it too, as ``unsupported Unicode escape sequence``, since
a NUL survives JSON serialisation as the escape ``\\u0000``. SQLite accepts it
silently, so the defect is invisible in development and fails in production.

Where it comes from
-------------------
Every text field this application writes originates outside it:

* **PDF text extraction** -- a page's content stream can carry NUL directly,
  and a malformed or padded CMap makes PyMuPDF emit one per unmapped glyph.
  This reaches ``document_pages.text``, ``page_blocks.text`` and, through the
  quote a claim is built from, ``claims.verbatim_quote``.
* **Vision and OCR readings** of scanned or chart-heavy pages, which land in
  ``page_understandings.recovered_text`` and again in claim quotes.
* **Model output** -- a truncated or repaired JSON response can carry a stray
  NUL through ``json.loads``, reaching claim statements, assessment rationales,
  report prose and diligence questions.
* **Retrieved literature** -- abstracts and titles from external APIs.

That is 183 text-bearing columns across 19 tables, so this is enforced once, at
the boundary, rather than at each field. See :func:`install_nul_guard`.
"""

from __future__ import annotations

from typing import Any

NUL = "\x00"


def scrub_nul(value: Any) -> Any:
    """Return `value` with every NUL removed from any string inside it.

    Recurses through the containers a JSON column can hold. Values without a
    NUL are returned unchanged -- by identity, not by copy -- which keeps the
    common case free and, importantly, preserves ``StrEnum`` members: they are
    ``str`` subclasses, and rebuilding one as a plain ``str`` would break the
    identity comparisons application code relies on.
    """
    if isinstance(value, str):
        return value.replace(NUL, "") if NUL in value else value
    if isinstance(value, dict):
        return {scrub_nul(key): scrub_nul(item) for key, item in value.items()}
    if isinstance(value, list):
        return [scrub_nul(item) for item in value]
    if isinstance(value, tuple):
        return tuple(scrub_nul(item) for item in value)
    if isinstance(value, set):
        return {scrub_nul(item) for item in value}
    return value


def contains_nul(value: Any) -> bool:
    """True when a NUL is present anywhere inside `value`."""
    if isinstance(value, str):
        return NUL in value
    if isinstance(value, dict):
        return any(contains_nul(k) or contains_nul(v) for k, v in value.items())
    if isinstance(value, (list, tuple, set)):
        return any(contains_nul(item) for item in value)
    return False


def scrub_instance(instance: Any) -> list[str]:
    """Scrub every loaded column attribute of a mapped instance in place.

    Returns the names of the attributes that were changed, which the caller can
    log: a NUL arriving here is worth knowing about even though it is handled.

    Only attributes already present in ``__dict__`` are touched. Reading through
    the descriptor would emit a lazy load for anything unloaded -- a query per
    attribute per object, during a flush.
    """
    from sqlalchemy import inspect as sa_inspect

    state = sa_inspect(instance)
    loaded = instance.__dict__
    changed: list[str] = []

    for attribute in state.mapper.column_attrs:
        key = attribute.key
        if key not in loaded:
            continue
        current = loaded[key]
        if current is None or not contains_nul(current):
            continue
        setattr(instance, key, scrub_nul(current))
        changed.append(key)

    return changed
