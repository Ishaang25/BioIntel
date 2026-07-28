"""Remove caches and local state.

Written in Python rather than as shell in the Makefile because the project is
developed on Windows as well as Linux, and `rm -rf` / `find -exec` are not
available in the Windows shell.

    python scripts/clean.py
"""

from __future__ import annotations

import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: Directories removed wholesale.
CACHE_DIRS = (
    "backend/.pytest_cache",
    "backend/.ruff_cache",
    "backend/.mypy_cache",
    "frontend/.next",
)

#: Glob patterns for local state files. Storage itself is kept: deleting a
#: developer's uploaded decks is not what "clean" should mean.
STATE_GLOBS = ("storage/*.db", "storage/*.db-wal", "storage/*.db-shm")


def main() -> None:
    removed = 0

    for relative in CACHE_DIRS:
        target = ROOT / relative
        if target.is_dir():
            shutil.rmtree(target, ignore_errors=True)
            print(f"removed {relative}")
            removed += 1

    for pattern in STATE_GLOBS:
        for path in ROOT.glob(pattern):
            path.unlink(missing_ok=True)
            print(f"removed {path.relative_to(ROOT).as_posix()}")
            removed += 1

    for pycache in ROOT.rglob("__pycache__"):
        # Never reach into the virtualenv or node_modules.
        if any(part in {".venv", "venv", "node_modules"} for part in pycache.parts):
            continue
        shutil.rmtree(pycache, ignore_errors=True)
        removed += 1

    for coverage in (ROOT / "backend" / ".coverage", ROOT / "backend" / "coverage.xml"):
        if coverage.exists():
            coverage.unlink()
            print(f"removed {coverage.relative_to(ROOT).as_posix()}")
            removed += 1

    print(f"clean: {removed} item(s) removed")


if __name__ == "__main__":
    main()
