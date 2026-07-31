'use client';

import { formatDuration, humanise } from '@/lib/format';
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
  running: 'Analysis in progress',
  succeeded: 'Analysis complete',
  failed: 'Analysis failed',
  cancelled: 'Analysis cancelled',
};

/**
 * Live progress for an in-flight analysis.
 *
 * Presentational only. Subscribing to the run is `useRunStream`'s job — this
 * used to own an EventSource *and* call `router.refresh()` to advance, which
 * re-ran the page on the server every few seconds.
 */
export function RunProgress({
  progress,
  streaming,
}: {
  progress: ProgressEvent;
  streaming: boolean;
}) {
  const percent = Math.round(progress.progress * 100);
  const failed = progress.status === 'failed';
  const done = progress.stages.filter((stage) => stage.status === 'succeeded').length;
  const settled = progress.status === 'succeeded' || failed || progress.status === 'cancelled';

  return (
    <Card className="p-6">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-[15px] font-semibold tracking-[-0.01em]">
          {HEADLINE[progress.status]}
        </h2>
        <span className="num text-[13px] text-fg-3">
          {done} of {progress.stages.length} stages · {percent}%
        </span>
      </div>

      <div
        className="mt-3 h-1.5 w-full overflow-hidden rounded-full bg-subtle"
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

      {failed && progress.error_message && (
        <div className="mt-4 rounded-lg border border-crit/25 bg-crit/10 px-3.5 py-2.5 text-[13px] leading-relaxed text-crit">
          {progress.error_message}
        </div>
      )}

      <ol className="mt-5 space-y-0">
        {progress.stages.map((stage) => (
          <li
            key={stage.stage}
            className="flex items-center gap-3 border-b border-line py-2 text-[13px] last:border-b-0"
          >
            <span className={cx('h-1.5 w-1.5 shrink-0 rounded-full', STAGE_DOT[stage.status])} />
            <span
              className={cx(
                'flex-1 truncate',
                stage.status === 'running'
                  ? 'font-medium text-fg'
                  : stage.status === 'succeeded'
                    ? 'text-fg-2'
                    : 'text-fg-3',
              )}
            >
              {humanise(stage.stage)}
            </span>
            {stage.error ? (
              <span className="max-w-xs truncate text-2xs text-crit" title={stage.error}>
                {stage.error}
              </span>
            ) : (
              stage.duration_ms !== null && (
                <span className="num text-2xs text-fg-3">{formatDuration(stage.duration_ms)}</span>
              )
            )}
          </li>
        ))}
      </ol>

      {!settled && (
        <p className="mt-4 text-2xs text-fg-3">
          {streaming
            ? 'Live updates connected. This page stays current on its own.'
            : 'Checking for updates every few seconds.'}
        </p>
      )}
    </Card>
  );
}
