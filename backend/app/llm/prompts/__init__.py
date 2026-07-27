"""Prompt library.

Prompts live in markdown files next to this module so they can be reviewed and
iterated on like any other artefact.  Placeholders use ``$name`` /
``${name}`` (``string.Template``) rather than ``str.format`` because prompts
contain literal JSON braces.

``PROMPT_VERSION`` is recorded on every run so that results can be traced back
to the exact wording that produced them.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from string import Template
from typing import Any

PROMPT_DIR = Path(__file__).parent

#: Bump on any semantic change to a prompt.  Recorded in ``AnalysisRun.config``.
PROMPT_VERSION = "1.0.0"


@lru_cache(maxsize=64)
def load(name: str) -> str:
    path = PROMPT_DIR / f"{name}.md"
    if not path.exists():
        raise FileNotFoundError(f"Prompt template not found: {name}")
    return path.read_text(encoding="utf-8").strip()


def render(name: str, /, **values: Any) -> str:
    """Render a prompt template, failing loudly on a missing placeholder."""
    return Template(load(name)).substitute(**values)


def system(name: str = "system_base") -> str:
    return load(name)


__all__ = ["PROMPT_VERSION", "load", "render", "system"]
