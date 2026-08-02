import Link from 'next/link';
import { notFound } from 'next/navigation';

import { StartRunButton } from '@/components/StartRunButton';
import { Badge, Card, EmptyState, MetricCard, PageHeader } from '@/components/ui';
import { api, ApiRequestError } from '@/lib/api';
import { formatBytes, formatDate, formatDuration, humanise } from '@/lib/format';
import { stageLabel } from '@/lib/run-progress';
import type { DocumentDetail, Run, RunStatus } from '@/lib/types';

export const dynamic = 'force-dynamic';

const STATUS_TONE: Record<RunStatus, 'pos' | 'crit' | 'info' | 'neutral'> = {
  succeeded: 'pos',
  failed: 'crit',
  running: 'info',
  pending: 'neutral',
  cancelled: 'neutral',
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
  const active = runs.some((run) => run.status === 'pending' || run.status === 'running');

  return (
    <div className="page space-y-6">
      <PageHeader
        title={document.filename}
        back={{ href: '/', label: 'All decks' }}
        subtitle={
          <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <span className="num">{document.page_count} pages</span>
            <span aria-hidden>·</span>
            <span className="num">{formatBytes(document.size_bytes)}</span>
            <span aria-hidden>·</span>
            <span>uploaded {formatDate(document.created_at)}</span>
            {document.requires_ocr && <Badge>Scanned</Badge>}
          </span>
        }
        actions={<StartRunButton documentId={documentId} hasActiveRun={active} />}
      />

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <MetricCard label="Pages" value={document.page_count} />
        <MetricCard label="Analyses" value={document.run_count} />
        <MetricCard label="Size" value={formatBytes(document.size_bytes)} />
        <MetricCard
          label="Text layer"
          value={document.requires_ocr ? 'Partial' : 'Complete'}
          hint={document.requires_ocr ? 'read visually' : 'digital PDF'}
        />
      </div>

      {document.notes && (
        <Card className="p-5">
          <h2 className="label">Analyst context</h2>
          <p className="mt-2 text-[13.5px] leading-relaxed text-fg-2">{document.notes}</p>
        </Card>
      )}

      <section>
        <h2 className="mb-3 text-[15px] font-semibold tracking-[-0.01em]">Analyses</h2>
        {runs.length === 0 ? (
          <EmptyState
            title="No analyses yet"
            description="Start an analysis to extract this deck's scientific claims and check them against the literature."
          />
        ) : (
          <Card className="overflow-hidden">
            <table className="data-table">
              <thead>
                <tr>
                  <th className="w-[190px]">Started</th>
                  <th className="w-[140px]">Status</th>
                  <th>Step</th>
                  <th className="w-[110px]">Duration</th>
                  <th className="w-[90px]" />
                </tr>
              </thead>
              <tbody>
                {runs.map((run) => (
                  <tr key={run.id}>
                    <td className="text-fg-2">{formatDate(run.created_at)}</td>
                    <td>
                      <Badge tone={STATUS_TONE[run.status]}>
                        {humanise(run.status)}
                        {run.status === 'running' && (
                          <span className="num">{Math.round(run.progress * 100)}%</span>
                        )}
                      </Badge>
                    </td>
                    <td className="text-fg-2">
                      {run.current_stage ? stageLabel(run.current_stage) : '—'}
                    </td>
                    <td className="num text-fg-2">{formatDuration(run.duration_ms)}</td>
                    <td className="text-right">
                      <Link
                        href={`/runs/${run.id}`}
                        className="text-fg-3 transition-colors hover:text-fg"
                      >
                        Open →
                      </Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>
        )}
      </section>
    </div>
  );
}
