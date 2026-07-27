'use client';

import { useRouter } from 'next/navigation';
import { useEffect, useRef, useState } from 'react';

import { humanise } from '@/components/ui';
import type { ProgressEvent, RunStatus, StageStatus } from '@/lib/types';

const TERMINAL: RunStatus[] = ['succeeded', 'failed', 'cancelled'];

const STATUS_DOT: Record<StageStatus, string> = {
  pending: 'bg-ink-300 dark:bg-ink-700',
  running: 'bg-sky-500 animate-pulse',
  succeeded: 'bg-emerald-500',
  failed: 'bg-red-500',
  skipped: 'bg-ink-400',
};

/**
 * Live progress for an in-flight analysis.
 *
 * Subscribes to the API's server-sent event stream and refreshes the server
 * component tree once the run reaches a terminal state, so the finished page
 * renders with real data rather than a client-side patchwork.
 */
export function RunProgress({ runId, initial }: { runId: string; initial: ProgressEvent }) {
  const router = useRouter();
  const [progress, setProgress] = useState<ProgressEvent>(initial);
  const [connected, setConnected] = useState(true);
  const refreshed = useRef(false);

  useEffect(() => {
    if (TERMINAL.includes(initial.status)) return;

    const source = new EventSource(`/api/proxy/runs/${runId}/events`);

    const onProgress = (event: MessageEvent<string>) => {
      try {
        const payload = JSON.parse(event.data) as ProgressEvent;
        setProgress(payload);
        if (TERMINAL.includes(payload.status) && !refreshed.current) {
          refreshed.current = true;
          source.close();
          router.refresh();
        }
      } catch {
        /* ignore malformed frames */
      }
    };

    source.addEventListener('progress', onProgress);
    source.addEventListener('done', onProgress);
    source.onopen = () => setConnected(true);
    source.onerror = () => setConnected(false);

    return () => source.close();
  }, [runId, initial.status, router]);

  // Fall back to polling if the event stream cannot be established.
  useEffect(() => {
    if (connected || TERMINAL.includes(progress.status)) return;
    const timer = setInterval(() => router.refresh(), 5000);
    return () => clearInterval(timer);
  }, [connected, progress.status, router]);

  const percent = Math.round(progress.progress * 100);
  const failed = progress.status === 'failed';

  return (
    <div className="card">
      <div className="flex items-baseline justify-between">
        <h2 className="text-base font-semibold">
          {failed
            ? 'Analysis failed'
            : progress.status === 'cancelled'
              ? 'Analysis cancelled'
              : progress.status === 'succeeded'
                ? 'Analysis complete'
                : 'Analysis in progress'}
        </h2>
        <span className="font-mono text-sm tabular-nums text-ink-500">{percent}%</span>
      </div>

      <div
        className="mt-3 h-1.5 w-full overflow-hidden rounded-full bg-ink-200 dark:bg-ink-800"
        role="progressbar"
        aria-valuenow={percent}
        aria-valuemin={0}
        aria-valuemax={100}
      >
        <div
          className={`h-full rounded-full transition-all duration-700 ${failed ? 'bg-red-500' : 'bg-ink-900 dark:bg-ink-100'}`}
          style={{ width: `${percent}%` }}
        />
      </div>

      {failed && progress.error_message && (
        <div className="mt-3 rounded-md border border-red-500/40 bg-red-500/5 px-3 py-2 text-sm text-red-700 dark:text-red-400">
          {progress.error_message}
        </div>
      )}

      <ol className="mt-5 space-y-2">
        {progress.stages.map((stage) => (
          <li key={stage.stage} className="flex items-center gap-3 text-sm">
            <span className={`h-2 w-2 shrink-0 rounded-full ${STATUS_DOT[stage.status]}`} />
            <span
              className={
                stage.status === 'succeeded'
                  ? 'text-ink-600 dark:text-ink-400'
                  : stage.status === 'running'
                    ? 'font-medium'
                    : 'text-ink-400'
              }
            >
              {humanise(stage.stage)}
            </span>
            {stage.duration_ms !== null && (
              <span className="ml-auto font-mono text-xs tabular-nums text-ink-400">
                {(stage.duration_ms / 1000).toFixed(1)}s
              </span>
            )}
            {stage.error && (
              <span className="ml-auto max-w-xs truncate text-xs text-red-500" title={stage.error}>
                {stage.error}
              </span>
            )}
          </li>
        ))}
      </ol>

      {!connected && !TERMINAL.includes(progress.status) && (
        <p className="mt-4 text-xs text-ink-500">
          Live updates unavailable; refreshing periodically instead.
        </p>
      )}
    </div>
  );
}
