"""Run the API and the background worker together.

    python scripts/run_stack.py [--port 8000]

`make api` and `make worker` each run one half; nothing analyses a deck until
both are up, so this is the target you want for a normal local session.
Ctrl-C stops both.
"""

from __future__ import annotations

import argparse
import signal
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"


def _python() -> str:
    """The virtualenv interpreter if there is one, else the current one."""
    candidates = (
        ROOT / ".venv" / "Scripts" / "python.exe",
        ROOT / ".venv" / "bin" / "python",
    )
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return sys.executable


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    python = _python()
    commands = {
        "api": [python, "-m", "uvicorn", "app.main:app", "--port", str(args.port)],
        "worker": [python, "-m", "app.jobs.worker"],
    }

    processes: dict[str, subprocess.Popen] = {}
    try:
        for name, command in commands.items():
            processes[name] = subprocess.Popen(command, cwd=BACKEND)
            print(f"started {name} (pid {processes[name].pid})")
        print(f"API on http://127.0.0.1:{args.port} -- Ctrl-C to stop")

        # Exit as soon as either half dies: a worker that has quit silently
        # looks exactly like a queue that is merely slow.
        while True:
            for name, process in processes.items():
                code = process.poll()
                if code is not None:
                    print(f"{name} exited with code {code}; shutting down")
                    return code or 1
            try:
                next(iter(processes.values())).wait(timeout=1)
            except subprocess.TimeoutExpired:
                continue
    except KeyboardInterrupt:
        print("\nstopping")
        return 0
    finally:
        for name, process in processes.items():
            if process.poll() is None:
                process.send_signal(signal.SIGTERM)
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()


if __name__ == "__main__":
    raise SystemExit(main())
