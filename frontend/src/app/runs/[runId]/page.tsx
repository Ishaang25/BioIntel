import { notFound } from 'next/navigation';

import { RunView } from '@/components/report/RunView';
import { api, ApiRequestError } from '@/lib/api';
import type { RunDetail } from '@/lib/types';

export const dynamic = 'force-dynamic';

/**
 * How long the server will wait for the run record before giving up on it.
 *
 * Short on purpose. This one call exists only to resolve a genuine 404 and to
 * let the shell render with the company name already in place; it is not on the
 * critical path. The backend runs analyses in-process, so while one is in
 * flight it can be slow to answer — and a Server Component that waits on a busy
 * backend is a function timeout, which is a blank page rather than a slow one.
 */
const RUN_LOOKUP_TIMEOUT_MS = 5_000;

/**
 * The run page.
 *
 * Renders immediately and never waits for an analysis. Progress arrives over
 * the backend's `/events` stream and the report is fetched by the client once
 * the run succeeds — see `RunView`. Everything this page used to await on the
 * server now happens in the browser, where a fifteen-minute analysis is a
 * progress bar rather than a timeout.
 */
export default async function RunPage({ params }: { params: Promise<{ runId: string }> }) {
  const { runId } = await params;

  let initialRun: RunDetail | null = null;
  try {
    initialRun = await api.getRun(runId, { timeoutMs: RUN_LOOKUP_TIMEOUT_MS });
  } catch (error) {
    // A run that does not exist is worth a 404. Anything else — a timeout, a
    // cold start, a saturated backend — is transient, and the client will
    // retry; it must not take the page down with it.
    if (error instanceof ApiRequestError && error.status === 404) notFound();
  }

  return <RunView runId={runId} initialRun={initialRun} />;
}
