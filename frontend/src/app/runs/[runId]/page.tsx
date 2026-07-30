import Link from 'next/link';
import { notFound } from 'next/navigation';

import { ReportWorkspace } from '@/components/report/ReportWorkspace';
import { RunProgress } from '@/components/RunProgress';
import { Callout, EmptyState, PageHeader } from '@/components/ui';
import { api, ApiRequestError, optional } from '@/lib/api';
import { formatDateTime, formatDuration, humanise } from '@/lib/format';
import type {
  Claim,
  Entity,
  Evidence,
  EvidenceLink,
  ProgressEvent,
  Question,
  Risk,
  RunDetail,
} from '@/lib/types';

export const dynamic = 'force-dynamic';

/** Bounded fan-out for the per-claim evidence fetch. */
async function mapWithConcurrency<T, R>(
  items: T[],
  limit: number,
  worker: (item: T) => Promise<R>,
): Promise<R[]> {
  const results: R[] = new Array(items.length);
  let cursor = 0;
  const runners = Array.from({ length: Math.min(limit, items.length) }, async () => {
    while (cursor < items.length) {
      const index = cursor;
      cursor += 1;
      results[index] = await worker(items[index]!);
    }
  });
  await Promise.all(runners);
  return results;
}

export default async function RunPage({ params }: { params: Promise<{ runId: string }> }) {
  const { runId } = await params;

  let run: RunDetail;
  try {
    run = await api.getRun(runId);
  } catch (error) {
    if (error instanceof ApiRequestError && error.status === 404) notFound();
    throw error;
  }

  const finished = run.status === 'succeeded';

  const [report, claims, questions, risks, entities, evidence] = finished
    ? await Promise.all([
        optional(api.getReport(runId)),
        api.listClaims(runId).then((page) => page.items),
        api.listQuestions(runId),
        api.listRisks(runId),
        api.listEntities(runId),
        api.listEvidence(runId),
      ])
    : [null, [] as Claim[], [] as Question[], [] as Risk[], [] as Entity[], [] as Evidence[]];

  // Evidence links live on the claim detail endpoint. Fetch only for the claims
  // that actually have adjudicated evidence, a few at a time so a large deck
  // does not open sixty sockets at once.
  const evidenceByClaim: Record<string, EvidenceLink[]> = {};
  if (finished && claims.length > 0) {
    const withEvidence = claims.filter(
      (claim) =>
        (claim.assessment?.supporting_count ?? 0) +
          (claim.assessment?.contradicting_count ?? 0) +
          (claim.assessment?.neutral_count ?? 0) >
        0,
    );
    const details = await mapWithConcurrency(withEvidence, 8, (claim) =>
      optional(api.getClaim(runId, claim.id)),
    );
    for (const detail of details) {
      if (detail) evidenceByClaim[detail.id] = detail.evidence_links;
    }
  }

  if (finished && report) {
    return (
      <ReportWorkspace
        run={run}
        report={report}
        claims={claims}
        questions={questions}
        risks={risks}
        entities={entities}
        evidence={evidence}
        evidenceByClaim={evidenceByClaim}
      />
    );
  }

  /* ------------------------------------------------- not finished (yet) --- */

  const company = run.profile?.company_name ?? run.document.filename;
  const progress: ProgressEvent = {
    run_id: run.id,
    status: run.status,
    current_stage: run.current_stage,
    progress: run.progress,
    error_code: run.error_code,
    error_message: run.error_message,
    stages: run.stages.map((stage) => ({
      stage: stage.stage,
      status: stage.status,
      duration_ms: stage.duration_ms,
      error: stage.error_message,
    })),
    counts: run.counts,
  };

  return (
    <div className="mx-auto w-full max-w-3xl px-6 py-10">
      <PageHeader
        title={company}
        back={{ href: `/documents/${run.document_id}`, label: run.document.filename }}
        subtitle={
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
        }
      />

      {run.status !== 'succeeded' && <RunProgress runId={runId} initial={progress} />}

      {run.status === 'failed' && (
        <div className="mt-4">
          <Callout tone="crit" title="This analysis did not complete">
            {run.error_message ?? 'The pipeline failed. Check the backend logs for detail.'}{' '}
            <Link href={`/documents/${run.document_id}`} className="underline underline-offset-2">
              Return to the document
            </Link>{' '}
            to start a new run.
          </Callout>
        </div>
      )}

      {finished && !report && (
        <div className="mt-4">
          <EmptyState
            title="No memo was produced"
            description="The analysis completed but the report stage did not write a memo. Check the run metrics for the stage error."
          />
        </div>
      )}
    </div>
  );
}
