"""Prefixed, sortable identifiers.

We use UUIDv7-style time-ordered random ids rendered as ``<prefix>_<hex>`` so
that ids are human-recognisable in logs and URLs, index well in B-trees, and
carry no sequential-guessing risk.
"""

from __future__ import annotations

import os
import time
import uuid

_PREFIXES = {
    "document": "doc",
    "run": "run",
    "page": "pg",
    "block": "blk",
    "claim": "clm",
    "entity": "ent",
    "evidence": "ev",
    "assessment": "asm",
    "question": "qst",
    "report": "rpt",
    "job": "job",
    "link": "lnk",
    "risk": "rsk",
    "artifact": "art",
}


def uuid7() -> uuid.UUID:
    """Generate a UUIDv7 (48-bit ms timestamp + 74 bits of randomness)."""
    ms = int(time.time() * 1000) & ((1 << 48) - 1)
    rand = int.from_bytes(os.urandom(10), "big")
    rand_a = (rand >> 62) & 0x0FFF  # 12 bits
    rand_b = rand & ((1 << 62) - 1)  # 62 bits
    value = (ms << 80) | (0x7 << 76) | (rand_a << 64) | (0b10 << 62) | rand_b
    return uuid.UUID(int=value)


def new_id(kind: str) -> str:
    """Return a new prefixed id for ``kind`` (e.g. ``run_0193f2...``)."""
    prefix = _PREFIXES.get(kind, kind[:3])
    return f"{prefix}_{uuid7().hex}"


def is_id(value: str, kind: str) -> bool:
    prefix = _PREFIXES.get(kind, kind[:3])
    return value.startswith(f"{prefix}_") and len(value) == len(prefix) + 33
