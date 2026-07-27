import Link from 'next/link';

import { EmptyState, PageHeader, formatDate, formatDuration } from '@/components/ui';
import { api } from '@/lib/api';

export const dynamic = 'force-dynamic';

const STATUS_STYLES: Record<string, string> = {
  succeeded: 'text-emerald-600 dark:text-emerald-400',
  failed: 'text-red-600 dark:text-red-400',
  running: 'text-sky-600 dark:text-sky-400',
  pending: 'text-ink-500',
  cancelled: 'text-ink-500',
};

export default async function RunsPage() {
  const page = await api.listRuns(50);

  return (
    <div>
      <PageHeader title="Analyses" subtitle={`${page.total} total`} />

      {page.items.length === 0 ? (
        <EmptyState
          title="No analyses yet"
          description="Upload a pitch deck to run your first scientific due-diligence analysis."
          action={
            <Link href="/" className="btn-primary">
              Upload a deck
            </Link>
          }
        />
      ) : (
        <div className="overflow-hidden rounded-lg border border-ink-200 dark:border-ink-800">
          <table className="w-full text-sm">
            <thead className="bg-ink-100 text-left dark:bg-ink-900">
              <tr>
                <th className="px-4 py-2.5 font-medium">Run</th>
                <th className="px-4 py-2.5 font-medium">Status</th>
                <th className="px-4 py-2.5 font-medium">Stage</th>
                <th className="px-4 py-2.5 font-medium">Duration</th>
                <th className="px-4 py-2.5 font-medium">Started</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-ink-200 bg-white dark:divide-ink-800 dark:bg-ink-950">
              {page.items.map((run) => (
                <tr key={run.id} className="hover:bg-ink-50 dark:hover:bg-ink-900">
                  <td className="px-4 py-3">
                    <Link href={`/runs/${run.id}`} className="font-mono text-xs hover:underline">
                      {run.id.slice(0, 16)}…
                    </Link>
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
                  <td className="px-4 py-3 text-ink-600 dark:text-ink-400">
                    {formatDate(run.created_at)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
