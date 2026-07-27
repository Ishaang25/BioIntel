"""Print where a run's time and money went.

    python -m app.scripts.profile_run [run_id] [--calls]

Stage durations come from ``run_stages``; the model calls behind them come
from ``llm_call_logs``.  Reading the two together is what turns "the entities
stage is slow" into "one call, 542 seconds, 21,871 input tokens, output capped
at 16,000" -- which names the fix.

Defaults to the most recent run.
"""

from __future__ import annotations

import argparse
import sys

from sqlalchemy import func, select

from app.db.models import AnalysisRun, LLMCallLog, RunStage
from app.db.session import session_scope

#: Flagged in the output: a call this large is the shape of the bug this
#: tooling was written to catch.
INPUT_TOKEN_CEILING = 20_000


def _latest_run_id() -> str | None:
    with session_scope() as session:
        return session.execute(
            select(AnalysisRun.id).order_by(AnalysisRun.created_at.desc()).limit(1)
        ).scalar_one_or_none()


def _stage_rows(run_id: str) -> list[tuple[str, str, int, str | None]]:
    with session_scope() as session:
        return [
            (row.stage.value, row.status.value, row.duration_ms or 0, row.error_message)
            for row in session.execute(
                select(RunStage).where(RunStage.run_id == run_id).order_by(RunStage.sequence)
            ).scalars()
        ]


def _call_summary(run_id: str) -> dict[str, dict[str, float]]:
    with session_scope() as session:
        rows = session.execute(
            select(
                LLMCallLog.stage,
                func.count(LLMCallLog.id),
                func.sum(LLMCallLog.latency_ms),
                func.max(LLMCallLog.latency_ms),
                func.sum(LLMCallLog.input_tokens),
                func.max(LLMCallLog.input_tokens),
                func.sum(LLMCallLog.output_tokens),
                func.max(LLMCallLog.output_tokens),
                func.sum(LLMCallLog.attempts),
                func.sum(LLMCallLog.estimated_cost_usd),
            )
            .where(LLMCallLog.run_id == run_id)
            .group_by(LLMCallLog.stage)
        ).all()
    return {
        (row[0] or "unattributed"): {
            "calls": row[1] or 0,
            "latency_total": row[2] or 0,
            "latency_max": row[3] or 0,
            "input_total": row[4] or 0,
            "input_max": row[5] or 0,
            "output_total": row[6] or 0,
            "output_max": row[7] or 0,
            "attempts": row[8] or 0,
            "cost": row[9] or 0.0,
        }
        for row in rows
    }


def _print_stages(run_id: str) -> None:
    stages = _stage_rows(run_id)
    calls = _call_summary(run_id)
    total_ms = sum(duration for _, _, duration, _ in stages)

    header = (
        f"{'stage':<20}{'status':<11}{'wall':>9}{'share':>7}"
        f"{'calls':>7}{'slowest':>9}{'in max':>8}{'out max':>9}{'cost':>9}"
    )
    print(header)
    print("-" * len(header))

    for stage, status, duration, error in stages:
        summary = calls.get(stage, {})
        share = (duration / total_ms * 100) if total_ms else 0.0
        flag = " <-- over input budget" if summary.get("input_max", 0) > INPUT_TOKEN_CEILING else ""
        print(
            f"{stage:<20}{status:<11}{duration / 1000:>8.1f}s{share:>6.0f}%"
            f"{int(summary.get('calls', 0)):>7}"
            f"{summary.get('latency_max', 0) / 1000:>8.1f}s"
            f"{int(summary.get('input_max', 0)):>8}"
            f"{int(summary.get('output_max', 0)):>9}"
            f"{summary.get('cost', 0.0):>9.4f}{flag}"
        )
        if error:
            print(f"{'':<20}{error[:100]}")

    retries = sum(int(s["attempts"]) - int(s["calls"]) for s in calls.values())
    print("-" * len(header))
    print(
        f"{'TOTAL':<20}{'':<11}{total_ms / 1000:>8.1f}s{'':>7}"
        f"{sum(int(s['calls']) for s in calls.values()):>7}"
        f"{'':>9}{'':>8}{'':>9}"
        f"{sum(s['cost'] for s in calls.values()):>9.4f}"
    )
    print(f"provider retries: {retries}")


def _print_calls(run_id: str) -> None:
    with session_scope() as session:
        rows = list(
            session.execute(
                select(LLMCallLog)
                .where(LLMCallLog.run_id == run_id)
                .order_by(LLMCallLog.latency_ms.desc())
            ).scalars()
        )
    print()
    header = (
        f"{'purpose':<24}{'stage':<20}{'latency':>9}{'in':>8}{'out':>8}"
        f"{'tries':>6}{'ok':>4}{'cost':>9}"
    )
    print(header)
    print("-" * len(header))
    for row in rows:
        print(
            f"{row.purpose:<24}{(row.stage or '-'):<20}{row.latency_ms / 1000:>8.1f}s"
            f"{row.input_tokens:>8}{row.output_tokens:>8}{row.attempts:>6}"
            f"{'y' if row.ok else 'n':>4}{row.estimated_cost_usd:>9.4f}"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Profile a BioIntel analysis run.")
    parser.add_argument("run_id", nargs="?", help="defaults to the most recent run")
    parser.add_argument("--calls", action="store_true", help="list every model call, slowest first")
    args = parser.parse_args(argv)

    run_id = args.run_id or _latest_run_id()
    if run_id is None:
        print("No runs found.", file=sys.stderr)
        return 1

    print(f"run {run_id}\n")
    _print_stages(run_id)
    if args.calls:
        _print_calls(run_id)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
