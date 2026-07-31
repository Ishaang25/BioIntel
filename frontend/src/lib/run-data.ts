/**
 * Loading the artefacts of a finished run.
 *
 * This deliberately lives outside any component. It used to run inside the
 * `/runs/[runId]` Server Component, which meant a page render waited on the
 * whole bundle — six list calls plus one detail call per claim carrying
 * evidence — against a backend that is often busy running the very analysis
 * being watched. On a serverless host that is a function timeout, not a slow
 * page. The work is identical; only the caller changed.
 */

import { api, optional, type RequestOptions } from './api';
import type { ReportInput } from './report-model';
import type { EvidenceLink, ProgressEvent, RunDetail } from './types';

/** Per-call ceiling inside the bundle. Generous: these are the big reads. */
const BUNDLE_TIMEOUT_MS = 20_000;

/** Concurrent claim-detail requests. Enough to be quick, few enough to be polite. */
const CLAIM_DETAIL_CONCURRENCY = 6;

/** Runs `worker` over `items`, never more than `limit` at a time. */
export async function mapWithConcurrency<T, R>(
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

/** Shapes a run record into the progress payload the SSE stream also emits. */
export function progressFromRun(run: RunDetail): ProgressEvent {
  return {
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
}

/**
 * Everything the report workspace needs, or `null` when the run finished
 * without producing a memo.
 *
 * `signal` lets a caller that navigates away stop the fan-out mid-flight.
 */
export async function loadReportBundle(
  runId: string,
  signal?: AbortSignal,
): Promise<ReportInput | null> {
  const options: RequestOptions = { timeoutMs: BUNDLE_TIMEOUT_MS, ...(signal ? { signal } : {}) };

  const [run, report, claims, questions, risks, entities, evidence] = await Promise.all([
    api.getRun(runId, options),
    optional(api.getReport(runId, options)),
    api.listClaims(runId, {}, options).then((page) => page.items),
    api.listQuestions(runId, options),
    api.listRisks(runId, options),
    api.listEntities(runId, options),
    api.listEvidence(runId, options),
  ]);

  if (!report) return null;

  // Evidence links live on the claim detail endpoint. Only claims that were
  // actually adjudicated have any, which is usually a small fraction.
  const evidenceByClaim: Record<string, EvidenceLink[]> = {};
  const withEvidence = claims.filter(
    (claim) =>
      (claim.assessment?.supporting_count ?? 0) +
        (claim.assessment?.contradicting_count ?? 0) +
        (claim.assessment?.neutral_count ?? 0) >
      0,
  );

  if (withEvidence.length > 0) {
    const details = await mapWithConcurrency(withEvidence, CLAIM_DETAIL_CONCURRENCY, (claim) =>
      optional(api.getClaim(runId, claim.id, options)),
    );
    for (const detail of details) {
      if (detail) evidenceByClaim[detail.id] = detail.evidence_links;
    }
  }

  return { run, report, claims, questions, risks, entities, evidence, evidenceByClaim };
}
