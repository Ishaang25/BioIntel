import Link from 'next/link';

import { Badge, Card, EmptyState, PageHeader } from '@/components/ui';
import { api } from '@/lib/api';
import { formatDate, formatDuration } from '@/lib/format';
import { stageLabel } from '@/lib/run-progress';
import type { Run, RunStatus } from '@/lib/types';

export const dynamic = 'force-dynamic';

const STATUS_TONE: Record<RunStatus, 'pos' | 'crit' | 'info' | 'neutral'> = {
  succeeded: 'pos',
  failed: 'crit',
  running: 'info',
  pending: 'neutral',
  cancelled: 'neutral',
};

const STATUS_LABEL: Record<RunStatus, string> = {
  succeeded: 'Complete',
  failed: 'Failed',
  running: 'Running',
  pending: 'Queued',
  cancelled: 'Cancelled',
};

export default async function RunsPage() {
  const page = await api.listRuns(50);

  return (
    <div className="page">
      <PageHeader
        title="Analyses"
        subtitle={
          page.total === 0
            ? 'Every deck you analyse appears here.'
            : `${page.total} ${page.total === 1 ? 'analysis' : 'analyses'}, newest first.`
        }
      />

      {page.items.length === 0 ? (
        <EmptyState
          title="No analyses yet"
          description="Upload a pitch deck and BioIntel will extract its scientific claims, check them against the published record, and write an IC memo. It takes a few minutes."
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
                <th>Analysis</th>
                <th className="w-[150px]">Status</th>
                <th className="w-[190px]">Step</th>
                <th className="w-[110px]">Duration</th>
                <th className="w-[150px]">Started</th>
              </tr>
            </thead>
            <tbody>
              {page.items.map((run) => (
                <tr key={run.id}>
                  <td>
                    <Link
                      href={`/runs/${run.id}`}
                      className="font-medium text-fg hover:underline"
                      title={run.id}
                    >
                      {runTitle(run)}
                    </Link>
                    {run.company_name && run.document_filename && (
                      <div className="mt-0.5 truncate text-2xs text-fg-3">
                        {run.document_filename}
                      </div>
                    )}
                  </td>
                  <td>
                    <Badge tone={STATUS_TONE[run.status]}>
                      {STATUS_LABEL[run.status]}
                      {run.status === 'running' && (
                        <span className="num">{Math.round(run.progress * 100)}%</span>
                      )}
                    </Badge>
                  </td>
                  <td className="text-fg-2">
                    {run.current_stage ? stageLabel(run.current_stage) : '—'}
                  </td>
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

/**
 * What the row is called.
 *
 * The run id was the only label here, which is the identifier the system uses
 * and not one anybody recognises. The company is what a reader is looking for;
 * the filename is the fallback until the profile stage has named it, and the id
 * survives as the link's title for anyone debugging.
 */
function runTitle(run: Run): string {
  return run.company_name || run.document_filename || run.id;
}
