import Link from 'next/link';
import { notFound } from 'next/navigation';

import { StartRunButton } from '@/components/StartRunButton';
import {
  Chip,
  EmptyState,
  PageHeader,
  Stat,
  formatBytes,
  formatDate,
  formatDuration,
} from '@/components/ui';
import { api, ApiRequestError } from '@/lib/api';
import type { DocumentDetail, Run } from '@/lib/types';

export const dynamic = 'force-dynamic';

const STATUS_STYLES: Record<string, string> = {
  succeeded: 'text-emerald-600 dark:text-emerald-400',
  failed: 'text-red-600 dark:text-red-400',
  running: 'text-sky-600 dark:text-sky-400',
  pending: 'text-ink-500',
  cancelled: 'text-ink-500',
};

export default async function DocumentPage({
  params,
}: {
  params: Promise<{ documentId: string }>;
}) {
  const { documentId } = await params;

  let document: DocumentDetail;
  try {
    document = await api.getDocument(documentId);
  } catch (error) {
    if (error instanceof ApiRequestError && error.status === 404) notFound();
    throw error;
  }

  const runs: Run[] = (await api.listRuns(50)).items.filter(
    (run) => run.document_id === documentId,
  );

  return (
    <div className="space-y-6">
      <PageHeader
        title={document.filename}
        back={{ href: '/', label: 'All decks' }}
        subtitle={
          <span className="flex flex-wrap items-center gap-2">
            <span>{document.page_count} pages</span>
            <span>·</span>
            <span>{formatBytes(document.size_bytes)}</span>
            <span>·</span>
            <span>uploaded {formatDate(document.created_at)}</span>
            {document.requires_ocr && <Chip>scanned</Chip>}
          </span>
        }
        actions={<StartRunButton documentId={documentId} hasActiveRun={runs.some((r) => r.status === 'pending' || r.status === 'running')} />}
      />

      <div className="card grid grid-cols-2 gap-6 sm:grid-cols-4">
        <Stat label="Pages" value={document.page_count} />
        <Stat label="Analyses" value={document.run_count} />
        <Stat label="Size" value={formatBytes(document.size_bytes)} />
        <Stat
          label="Text layer"
          value={document.requires_ocr ? 'Partial' : 'Complete'}
          hint={document.requires_ocr ? 'read visually' : 'digital PDF'}
        />
      </div>

      {document.notes && (
        <div className="card">
          <h2 className="label">Analyst context</h2>
          <p className="mt-1.5 text-sm">{document.notes}</p>
        </div>
      )}

      <section>
        <h2 className="mb-3 text-base font-semibold">Analyses</h2>
        {runs.length === 0 ? (
          <EmptyState
            title="No analyses yet"
            description="Start an analysis to extract this deck's scientific claims and check them against the literature."
          />
        ) : (
          <div className="overflow-hidden rounded-lg border border-ink-200 dark:border-ink-800">
            <table className="w-full text-sm">
              <thead className="bg-ink-100 text-left dark:bg-ink-900">
                <tr>
                  <th className="px-4 py-2.5 font-medium">Started</th>
                  <th className="px-4 py-2.5 font-medium">Status</th>
                  <th className="px-4 py-2.5 font-medium">Stage</th>
                  <th className="px-4 py-2.5 font-medium">Duration</th>
                  <th className="px-4 py-2.5" />
                </tr>
              </thead>
              <tbody className="divide-y divide-ink-200 bg-white dark:divide-ink-800 dark:bg-ink-950">
                {runs.map((run) => (
                  <tr key={run.id} className="hover:bg-ink-50 dark:hover:bg-ink-900">
                    <td className="px-4 py-3 text-ink-600 dark:text-ink-400">
                      {formatDate(run.created_at)}
                    </td>
                    <td className={`px-4 py-3 font-medium ${STATUS_STYLES[run.status] ?? ''}`}>
                      {run.status}
                      {run.status === 'running' && ` (${Math.round(run.progress * 100)}%)`}
                    </td>
                    <td className="px-4 py-3 text-ink-600 dark:text-ink-400">
                      {run.current_stage?.replace(/_/g, ' ') ?? '—'}
                    </td>
                    <td className="px-4 py-3 tabular-nums text-ink-600 dark:text-ink-400">
                      {formatDuration(run.duration_ms)}
                    </td>
                    <td className="px-4 py-3 text-right">
                      <Link
                        href={`/runs/${run.id}`}
                        className="text-ink-500 hover:text-ink-900 dark:hover:text-ink-100"
                      >
                        Open →
                      </Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
