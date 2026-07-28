"""Deduplication utilities for common operations."""

from __future__ import annotations


def dedupe_strings(values: list[str], *, case_sensitive: bool = False) -> list[str]:
    """Deduplicate a list of strings, preserving order.

    Args:
        values: List of strings to deduplicate
        case_sensitive: If False, comparison is case-insensitive; returns first occurrence

    Returns:
        Deduplicated list in original order
    """
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        key = value if case_sensitive else value.strip().lower()
        if key not in seen:
            seen.add(key)
            out.append(value)
    return out
