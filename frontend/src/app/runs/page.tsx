import Link from 'next/link';

import { Badge, Card, EmptyState, PageHeader } from '@/components/ui';
import { api } from '@/lib/api';
import { formatDate, formatDuration, humanise } from '@/lib/format';
import type { RunStatus } from '@/lib/types';

export const dynamic = 'force-dynamic';

const STATUS_TONE: Record<RunStatus, 'pos' | 'crit' | 'info' | 'neutral'> = {
  succeeded: 'pos',
  failed: 'crit',
  running: 'info',
  pending: 'neutral',
  cancelled: 'neutral',
};

export default async function RunsPage() {
  const page = await api.listRuns(50);

  return (
    <div className="page">
      <PageHeader title="Analyses" subtitle={`${page.total} total`} />

      {page.items.length === 0 ? (
        <EmptyState
          title="No analyses yet"
          description="Upload a pitch deck to run your first scientific due-diligence analysis."
          action={
            <Link href="/" className="btn btn-sm btn-primary">
              Upload a deck
            </Link>
          }
        />
      ) : (
        <Card className="overflow-hidden">
          <table className="data-table">
            <thead>
              <tr>
                <th>Run</th>
                <th className="w-[140px]">Status</th>
                <th className="w-[180px]">Stage</th>
                <th className="w-[110px]">Duration</th>
                <th className="w-[190px]">Started</th>
              </tr>
            </thead>
            <tbody>
              {page.items.map((run) => (
                <tr key={run.id}>
                  <td>
                    <Link
                      href={`/runs/${run.id}`}
                      className="num font-mono text-2xs text-fg hover:underline"
                    >
                      {run.id}
                    </Link>
                  </td>
                  <td>
                    <Badge tone={STATUS_TONE[run.status]}>
                      {humanise(run.status)}
                      {run.status === 'running' && (
                        <span className="num">{Math.round(run.progress * 100)}%</span>
                      )}
                    </Badge>
                  </td>
                  <td className="text-fg-2">{humanise(run.current_stage) || '—'}</td>
                  <td className="num text-fg-2">{formatDuration(run.duration_ms)}</td>
                  <td className="text-fg-2">{formatDate(run.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}
    </div>
  );
}
