'use client';

import { humanise } from '@/lib/format';
import { sourceLabel } from '@/lib/report-model';
import { Badge, Card, EmptyState, cx } from '@/components/ui/primitives';
import { BarList, ChartLegend, DonutChart } from '@/components/ui/charts';
import { useReport } from '../context';
import { Section } from './Section';

/**
 * Where the evidence came from and what it did.
 *
 * The distributions are the fastest read in the report: the shape of the donut
 * says immediately whether this deck was checkable at all, and each segment is
 * a filter into the claim explorer rather than a dead figure.
 */
export function EvidenceSection() {
  const { model, filterClaims } = useReport();
  const totalClaims = model.claims.length;

  return (
    <Section
      id="evidence"
      title="Evidence distribution"
      description="How the extracted claims resolved against the outside record, and which registries and literature sources were searched to get there."
    >
      <div className="grid gap-4 lg:grid-cols-3">
        <Card className="p-5">
          <h3 className="label">Claims by evidence state</h3>
          {/* Stacked rather than side-by-side: "Not independently verified" is
              the label that must never be truncated. */}
          <div className="mt-4 flex justify-center">
            <DonutChart
              slices={model.evidenceStateSlices}
              centreValue={totalClaims}
              centreLabel="claims"
            />
          </div>
          <div className="mt-4">
            <ChartLegend slices={model.evidenceStateSlices} sum={totalClaims} />
          </div>
          <p className="mt-4 border-t border-line pt-3 text-2xs leading-relaxed text-fg-3">
            Unverified is not a negative finding. It records that no independent source capable of
            settling the claim was reachable.
          </p>
        </Card>

        <Card className="p-5">
          <h3 className="label">Claims by type</h3>
          <p className="mt-1 text-2xs text-fg-3">Select a bar to filter the claim explorer.</p>
          <div className="mt-3">
            {model.claimTypeSlices.length === 0 ? (
              <p className="text-[13px] text-fg-3">No claims were extracted.</p>
            ) : (
              <BarList
                slices={model.claimTypeSlices}
                onSelect={(key) => filterClaims({ category: key })}
              />
            )}
          </div>
        </Card>

        <Card className="p-5">
          <h3 className="label">Sources searched</h3>
          <div className="mt-3">
            {model.sourceSlices.length === 0 ? (
              <p className="text-[13px] text-fg-3">
                No literature search was recorded for this run.
              </p>
            ) : (
              <BarList slices={model.sourceSlices} />
            )}
          </div>

          <dl className="mt-4 space-y-2 border-t border-line pt-3 text-[13px]">
            <Fact label="Records screened" value={model.retrieval.recordsSeen} />
            <Fact label="Records retained" value={model.retrieval.recordsRetained} />
            <Fact label="Claims searched" value={model.retrieval.claimsSearched} />
          </dl>

          {model.stanceSlices.length > 0 && (
            <div className="mt-4 border-t border-line pt-3">
              <h4 className="label">Adjudicated stance</h4>
              <div className="mt-2">
                <BarList slices={model.stanceSlices} />
              </div>
            </div>
          )}
        </Card>
      </div>

      <div className="mt-4">
        <div className="mb-3 flex flex-wrap items-end justify-between gap-2">
          <h3 className="text-[15px] font-semibold tracking-[-0.01em] text-fg">
            Retrieved records
          </h3>
          <span className="num text-[13px] text-fg-3">{model.evidence.length} records</span>
        </div>

        {model.evidence.length === 0 ? (
          <EmptyState
            compact
            title="No literature records were retained"
            description="Retrieval ran but nothing cleared the relevance threshold for these claims. That limits confidence; it does not lower the score."
          />
        ) : (
          <Card className="overflow-hidden">
            <div className="overflow-x-auto">
              <table className="w-full min-w-[720px] text-[13px]">
                <thead>
                  <tr className="border-b border-line">
                    <th className="label px-4 py-2 text-left">Record</th>
                    <th className="label w-[130px] px-4 py-2 text-left">Source</th>
                    <th className="label w-[150px] px-4 py-2 text-left">Design</th>
                    <th className="label w-[150px] px-4 py-2 text-left">Identifier</th>
                    <th className="label w-[64px] px-4 py-2 text-right">Year</th>
                  </tr>
                </thead>
                <tbody>
                  {model.evidence.map((record) => (
                    <tr
                      key={record.id}
                      className="border-b border-line transition-colors last:border-b-0 hover:bg-subtle"
                    >
                      <td className="max-w-0 px-4 py-2.5">
                        <div className="flex items-center gap-2">
                          {record.url ? (
                            <a
                              href={record.url}
                              target="_blank"
                              rel="noreferrer noopener"
                              className="truncate text-fg hover:underline"
                              title={record.title}
                            >
                              {record.title}
                            </a>
                          ) : (
                            <span className="truncate text-fg" title={record.title}>
                              {record.title}
                            </span>
                          )}
                          {record.is_retracted && <Badge tone="crit">Retracted</Badge>}
                          {record.is_preprint && <Badge tone="warn">Preprint</Badge>}
                        </div>
                        {record.journal && (
                          <div className="mt-0.5 truncate text-2xs text-fg-3">{record.journal}</div>
                        )}
                      </td>
                      <td className="px-4 py-2.5 text-fg-2">{sourceLabel(record.source)}</td>
                      <td className="px-4 py-2.5 text-fg-2">
                        {record.study_design ? humanise(record.study_design) : '—'}
                      </td>
                      <td className={cx('px-4 py-2.5 num font-mono text-2xs text-fg-2')}>
                        {record.pmid
                          ? `PMID ${record.pmid}`
                          : (record.nct_id ?? record.doi ?? record.external_id ?? '—')}
                      </td>
                      <td className="num px-4 py-2.5 text-right text-fg-2">
                        {record.publication_year ?? '—'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        )}
      </div>
    </Section>
  );
}

function Fact({ label, value }: { label: string; value: number | null }) {
  if (value === null) return null;
  return (
    <div className="flex items-baseline justify-between gap-3">
      <dt className="text-fg-2">{label}</dt>
      <dd className="num font-medium text-fg">{value}</dd>
    </div>
  );
}
