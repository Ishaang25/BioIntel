'use client';

import Link from 'next/link';
import { useEffect, useRef, useState } from 'react';

import { formatDateTime, formatDuration, humanise } from '@/lib/format';
import type { ReportInput } from '@/lib/report-model';
import { loadReportBundle } from '@/lib/run-data';
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
  const requested = useRef(false);

  const succeeded = progress?.status === 'succeeded';

  useEffect(() => {
    if (!succeeded || requested.current) return;
    requested.current = true;

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
  }, [succeeded, runId]);

  if (state === 'ready' && bundle) {
    return <ReportWorkspace {...bundle} />;
  }

  const company = run?.profile?.company_name ?? run?.document.filename ?? 'Analysis';

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
              <span>{humanise(run.status)}</span>
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
        <RunProgress progress={progress} streaming={streaming} />
      )}

      {state === 'idle' && !progress && !error && <ConnectingSkeleton />}

      {state === 'absent' && (
        <EmptyState
          title="No memo was produced"
          description="The analysis completed but the report stage did not write a memo. Check the run metrics for the stage error."
        />
      )}

      {state === 'error' && (
        <Callout tone="crit" title="The report could not be loaded">
          The analysis finished, but its report did not come back. This is usually the API being
          busy rather than anything wrong with the run.{' '}
          <button
            type="button"
            className="underline underline-offset-2"
            onClick={() => {
              requested.current = false;
              setState('idle');
            }}
          >
            Try again
          </button>
        </Callout>
      )}

      {error && state === 'idle' && (
        <div className="mt-4">
          <Callout tone="warn" title="Cannot reach the API">
            {error} Retrying automatically.
          </Callout>
        </div>
      )}

      {progress?.status === 'failed' && run && (
        <div className="mt-4">
          <Callout tone="crit" title="This analysis did not complete">
            {progress.error_message ?? 'The pipeline failed. Check the backend logs for detail.'}{' '}
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
            Loading the claims, evidence and memo…
          </p>
        </div>
      </div>
      <div className="mt-5 space-y-2.5">
        {[0, 1, 2, 3].map((row) => (
          <div key={row} className="h-3 animate-pulse rounded bg-subtle" style={{ width: `${92 - row * 13}%` }} />
        ))}
      </div>
    </Card>
  );
}

function ConnectingSkeleton() {
  return (
    <Card className="p-6" aria-busy="true" aria-label="Connecting">
      <div className="h-4 w-40 animate-pulse rounded bg-subtle" />
      <div className="mt-4 h-1.5 w-full animate-pulse rounded-full bg-subtle" />
      <div className="mt-5 space-y-2">
        {[0, 1, 2, 3, 4].map((row) => (
          <div key={row} className="h-3 animate-pulse rounded bg-subtle" />
        ))}
      </div>
    </Card>
  );
}
