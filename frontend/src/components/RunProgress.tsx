'use client';

import { useEffect, useState } from 'react';

import { formatDuration } from '@/lib/format';
import {
  STAGE_NARRATIVE,
  currentActivityLine,
  estimateRemainingMs,
  formatRemaining,
  stageLabel,
} from '@/lib/run-progress';
import { Card, cx } from '@/components/ui';
import type { ProgressEvent, RunStatus, StageStatus } from '@/lib/types';

const STAGE_DOT: Record<StageStatus, string> = {
  pending: 'bg-line-strong',
  running: 'bg-info motion-safe:animate-pulse',
  succeeded: 'bg-pos',
  failed: 'bg-crit',
  skipped: 'bg-fg-3',
};

const HEADLINE: Record<RunStatus, string> = {
  pending: 'Queued',
  running: 'Analysing the deck',
  succeeded: 'Analysis complete',
  failed: 'Analysis failed',
  cancelled: 'Analysis cancelled',
};

const SUBHEAD: Record<RunStatus, string> = {
  pending:
    'Waiting for a worker to pick this up. It normally starts within a few seconds.',
  running:
    'This takes a few minutes. You can leave this page — the analysis continues without it.',
  succeeded: 'Loading the memo.',
  failed: 'Nothing was charged for the incomplete stages.',
  cancelled: 'This run was stopped before it finished.',
};

/**
 * Live progress for an in-flight analysis.
 *
 * The whole job of this panel is to make a multi-minute wait legible. It says
 * what is happening now — from the backend's own sub-step where there is one —
 * roughly how much is left, and, for the stage in flight, why that step takes
 * the time it does. A bar alone cannot do that: the report stage is 5% of the
 * weight and can be two minutes of it, which reads as frozen.
 *
 * Presentational only. Subscribing to the run is `useRunStream`'s job.
 */
export function RunProgress({
  progress,
  streaming,
  startedAt,
}: {
  progress: ProgressEvent;
  streaming: boolean;
  /** ISO timestamp the run began, used to estimate what is left. */
  startedAt?: string | null;
}) {
  const percent = Math.round(progress.progress * 100);
  const failed = progress.status === 'failed';
  const done = progress.stages.filter((stage) => stage.status === 'succeeded').length;
  const settled = progress.status === 'succeeded' || failed || progress.status === 'cancelled';
  const activity = currentActivityLine(progress.current_stage, progress.current_activity);
  const remaining = useRemaining(progress.progress, startedAt, settled);

  return (
    <Card className="p-6">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <h2 className="text-[15px] font-semibold tracking-[-0.01em]">
          {HEADLINE[progress.status]}
        </h2>
        <span className="num text-[13px] text-fg-3">
          {done} of {progress.stages.length} steps · {percent}%
        </span>
      </div>
      <p className="mt-1 text-[13px] leading-relaxed text-fg-2">{SUBHEAD[progress.status]}</p>

      <div
        className="mt-3.5 h-1.5 w-full overflow-hidden rounded-full bg-subtle"
        role="progressbar"
        aria-valuenow={percent}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label="Analysis progress"
      >
        <div
          className={cx(
            'h-full rounded-full transition-[width] duration-500',
            failed ? 'bg-crit' : 'bg-fg',
          )}
          style={{ width: `${percent}%` }}
        />
      </div>

      {!settled && (
        <div className="mt-3 flex flex-wrap items-baseline gap-x-2 gap-y-1" aria-live="polite">
          {activity && (
            <span className="text-[13.5px] font-medium text-fg">
              {activity}
              <span aria-hidden className="text-fg-3">
                …
              </span>
            </span>
          )}
          {remaining && <span className="text-[13px] text-fg-3">· {remaining}</span>}
        </div>
      )}

      {failed && progress.error_message && (
        <div className="mt-4 rounded-lg border border-crit/25 bg-crit/10 px-3.5 py-2.5 text-[13px] leading-relaxed text-crit">
          {progress.error_message}
        </div>
      )}

      <ol className="mt-5 space-y-0">
        {progress.stages.map((stage) => {
          const narrative = STAGE_NARRATIVE[stage.stage];
          const running = stage.status === 'running';
          return (
            <li
              key={stage.stage}
              className="border-b border-line py-2 text-[13px] last:border-b-0"
            >
              <div className="flex items-center gap-3">
                <span
                  aria-hidden
                  className={cx('h-1.5 w-1.5 shrink-0 rounded-full', STAGE_DOT[stage.status])}
                />
                <span
                  className={cx(
                    'flex-1 truncate',
                    running
                      ? 'font-medium text-fg'
                      : stage.status === 'succeeded'
                        ? 'text-fg-2'
                        : 'text-fg-3',
                  )}
                >
                  {stageLabel(stage.stage)}
                </span>
                {stage.error ? (
                  <span className="max-w-xs truncate text-2xs text-crit" title={stage.error}>
                    {stage.error}
                  </span>
                ) : stage.status === 'skipped' ? (
                  <span className="text-2xs text-fg-3">Skipped</span>
                ) : (
                  stage.duration_ms !== null && (
                    <span className="num text-2xs text-fg-3">
                      {formatDuration(stage.duration_ms)}
                    </span>
                  )
                )}
              </div>
              {/* Only the stage in flight explains itself. Expanding all ten
                  would turn a status panel into a wall of text. */}
              {running && narrative && (
                <p className="ml-[18px] mt-1 max-w-prose text-2xs leading-relaxed text-fg-3">
                  {narrative.why}
                </p>
              )}
            </li>
          );
        })}
      </ol>

      {!settled && (
        <p className="mt-4 text-2xs text-fg-3">
          {streaming
            ? 'Live updates connected — this page stays current on its own.'
            : 'Live updates are unavailable here, so this page is checking every few seconds instead. Progress is unaffected.'}
        </p>
      )}
    </Card>
  );
}

/**
 * Re-derives the estimate on a slow tick as well as on each update.
 *
 * Without the tick the figure would only change when the backend reports, so a
 * stage that is quiet for ninety seconds would keep claiming the same time
 * remaining throughout — the estimate would look frozen exactly when the
 * reader is most doubtful that anything is happening.
 */
function useRemaining(
  progress: number,
  startedAt: string | null | undefined,
  settled: boolean,
): string | null {
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (settled) return;
    const timer = setInterval(() => setNow(Date.now()), 5_000);
    return () => clearInterval(timer);
  }, [settled]);

  if (settled || !startedAt) return null;
  const started = Date.parse(/[Z+]/.test(startedAt) ? startedAt : `${startedAt}Z`);
  if (Number.isNaN(started)) return null;

  const estimate = estimateRemainingMs(progress, now - started);
  return estimate === null ? null : formatRemaining(estimate);
}
