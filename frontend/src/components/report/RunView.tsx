'use client';

import Link from 'next/link';
import { useEffect, useState } from 'react';

import { formatDateTime, formatDuration } from '@/lib/format';
import type { ReportInput } from '@/lib/report-model';
import { loadReportBundle } from '@/lib/run-data';
import { stageLabel } from '@/lib/run-progress';
import { useRunStream } from '@/lib/use-run-stream';
import type { RunDetail } from '@/lib/types';
import { Callout, Card, EmptyState, PageHeader } from '@/components/ui';
import { RunProgress } from '@/components/RunProgress';
import { ReportWorkspace } from './ReportWorkspace';

type BundleState = 'idle' | 'loading' | 'ready' | 'absent' | 'error';

/**
 * The whole `/runs/[runId]` experience, driven from the client.
 *
 * The page renders the moment the route resolves. Progress arrives over the
 * event stream; when the run reaches `succeeded` the report is fetched here and
 * the workspace replaces the progress panel in place. Nothing on the server
 * ever waits for an analysis, so a run that takes fifteen minutes costs the
 * same page render as one that is already finished.
 */
export function RunView({ runId, initialRun }: { runId: string; initialRun: RunDetail | null }) {
  const { run, progress, streaming, error } = useRunStream(runId, initialRun);

  const [bundle, setBundle] = useState<ReportInput | null>(null);
  const [state, setState] = useState<BundleState>('idle');
  /**
   * Bumped to ask for the report again. This used to be a ref cleared by the
   * retry button, which meant "Try again" changed no dependency and the effect
   * never re-ran: the button reset the panel and then sat in the idle state
   * forever. Retrying is a new attempt, so it has to be state.
   */
  const [attempt, setAttempt] = useState(0);

  const succeeded = progress?.status === 'succeeded';

  useEffect(() => {
    if (!succeeded) return;

    const controller = new AbortController();
    setState('loading');

    loadReportBundle(runId, controller.signal)
      .then((result) => {
        if (controller.signal.aborted) return;
        if (result) {
          setBundle(result);
          setState('ready');
        } else {
          setState('absent');
        }
      })
      .catch(() => {
        if (!controller.signal.aborted) setState('error');
      });

    return () => controller.abort();
  }, [succeeded, runId, attempt]);

  if (state === 'ready' && bundle) {
    return <ReportWorkspace {...bundle} />;
  }

  const company = run?.profile?.company_name ?? run?.document.filename ?? 'Analysis';
  const failed = progress?.status === 'failed';

  return (
    <div className="mx-auto w-full max-w-3xl px-6 py-10">
      <PageHeader
        title={company}
        back={
          run
            ? { href: `/documents/${run.document_id}`, label: run.document.filename }
            : { href: '/runs', label: 'All analyses' }
        }
        subtitle={
          run ? (
            <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
              <span>{stageStatusLine(run)}</span>
              {run.duration_ms !== null && (
                <>
                  <span aria-hidden>·</span>
                  <span className="num">{formatDuration(run.duration_ms)}</span>
                </>
              )}
              <span aria-hidden>·</span>
              <span>started {formatDateTime(run.created_at)}</span>
            </span>
          ) : (
            <span className="num text-fg-3">{runId}</span>
          )
        }
      />

      {state === 'loading' && <ReportLoadingSkeleton />}

      {state === 'idle' && progress && (
        <RunProgress
          progress={progress}
          streaming={streaming}
          startedAt={run?.started_at ?? run?.created_at ?? null}
        />
      )}

      {state === 'idle' && !progress && !error && <ConnectingSkeleton />}

      {state === 'absent' && (
        <EmptyState
          title="This analysis finished without a memo"
          description="Every earlier stage completed, but the report stage did not write one — usually a language-model timeout on the final call. The extracted claims and evidence are intact, so re-running only repeats the last step."
          action={
            run && (
              <Link className="btn btn-primary" href={`/documents/${run.document_id}`}>
                Start a new analysis
              </Link>
            )
          }
        />
      )}

      {state === 'error' && (
        <Callout tone="crit" title="The memo could not be loaded">
          The analysis itself finished — this is the report failing to come back, which is almost
          always the API being busy rather than anything wrong with the run. Nothing has been lost.{' '}
          <button
            type="button"
            className="underline underline-offset-2"
            onClick={() => setAttempt((value) => value + 1)}
          >
            Try again
          </button>
        </Callout>
      )}

      {error && state === 'idle' && (
        <div className="mt-4">
          <Callout tone="warn" title="Not receiving updates">
            {error} The analysis is unaffected — it runs on the server, not in this tab. This page
            keeps retrying and will catch up on its own.
          </Callout>
        </div>
      )}

      {failed && run && (
        <div className="mt-4">
          <Callout tone="crit" title="This analysis did not complete">
            {progress?.error_message ??
              'The pipeline stopped before finishing. The stage list above shows where.'}{' '}
            Re-running is usually worth one attempt: most failures here are timeouts against an
            external source rather than anything about the deck.{' '}
            <Link href={`/documents/${run.document_id}`} className="underline underline-offset-2">
              Return to the document
            </Link>{' '}
            to start a new run.
          </Callout>
        </div>
      )}
    </div>
  );
}

/** "Running · Weighing the evidence" rather than a bare status enum. */
function stageStatusLine(run: RunDetail): string {
  if (run.status !== 'running') {
    return run.status.charAt(0).toUpperCase() + run.status.slice(1);
  }
  return run.current_stage ? `Running · ${stageLabel(run.current_stage)}` : 'Running';
}

function ReportLoadingSkeleton() {
  return (
    <Card className="p-6" aria-busy="true" aria-label="Loading the report">
      <div className="flex items-center gap-3">
        <span
          aria-hidden
          className="h-3.5 w-3.5 shrink-0 rounded-full border-2 border-line border-t-fg motion-safe:animate-spin"
        />
        <div>
          <p className="text-[14px] font-medium text-fg">Analysis complete</p>
          <p className="mt-0.5 text-[13px] text-fg-2">
            Fetching the memo, claims and evidence. A few seconds.
          </p>
        </div>
      </div>
      <div className="mt-5 space-y-2.5">
        {[0, 1, 2, 3].map((row) => (
          <div
            key={row}
            className="h-3 animate-pulse rounded bg-subtle"
            style={{ width: `${92 - row * 13}%` }}
          />
        ))}
      </div>
    </Card>
  );
}

function ConnectingSkeleton() {
  return (
    <Card className="p-6" aria-busy="true" aria-label="Connecting">
      <p className="text-[14px] font-medium text-fg">Connecting to the analysis…</p>
      <p className="mt-0.5 text-[13px] text-fg-2">
        Fetching this run&rsquo;s current state. If it has already finished, the memo opens
        directly.
      </p>
      <div className="mt-4 h-1.5 w-full animate-pulse rounded-full bg-subtle" />
      <div className="mt-5 space-y-2">
        {[0, 1, 2, 3, 4].map((row) => (
          <div key={row} className="h-3 animate-pulse rounded bg-subtle" />
        ))}
      </div>
    </Card>
  );
}
