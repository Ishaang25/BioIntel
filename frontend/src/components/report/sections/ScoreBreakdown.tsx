'use client';

import { BAND_LABEL, BAND_TONE, type ScoreFacts } from '@/lib/report-model';
import type { DimensionScore } from '@/lib/types';
import { Badge, Card, EmptyState, cx, toneClasses } from '@/components/ui/primitives';
import { Tooltip } from '@/components/ui/Tooltip';
import { useReport } from '../context';
import { Section } from './Section';

/**
 * The score, taken apart.
 *
 * A dimension with no informing claims renders as *not assessed*, never as a
 * zero: "the deck said nothing about this" and "we found a problem here" are
 * different findings and must not share a visual.
 */
export function ScoreBreakdownSection() {
  const { model } = useReport();
  const assessed = model.dimensions.filter((dimension) => dimension.assessed);
  const unassessed = model.dimensions.filter((dimension) => !dimension.assessed);

  return (
    <Section
      id="score"
      title="Score breakdown"
      description="Computed deterministically from the claim-level analysis. Dimension weighting is set by company archetype, because a platform company and a single-asset company do not carry the same risks."
    >
      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,300px)]">
        <Card className="p-5">
          {assessed.length === 0 ? (
            <EmptyState
              compact
              title="No dimension was assessed"
              description="The scorecard stage did not attribute any claim to a scoring dimension."
            />
          ) : (
            <ul className="divide-y divide-line">
              {assessed.map((dimension) => (
                <DimensionRow key={dimension.dimension} dimension={dimension} />
              ))}
            </ul>
          )}

          {unassessed.length > 0 && (
            <div className="mt-5 rounded-lg border border-dashed border-line px-4 py-3">
              <h3 className="label">Not assessed · {unassessed.length}</h3>
              <p className="mt-1 text-2xs leading-relaxed text-fg-3">
                No claim in the deck bore on these. That is a disclosure gap, not a negative
                finding, and nothing was scored for them.
              </p>
              <ul className="mt-2.5 grid gap-x-6 gap-y-1.5 sm:grid-cols-2">
                {unassessed.map((dimension) => (
                  <li key={dimension.dimension} className="text-[13px]">
                    <Tooltip
                      content={
                        <>
                          <strong className="text-fg">{dimension.label}</strong>
                          <p className="mt-1">{dimension.question}</p>
                          {dimension.rationale && <p className="mt-1.5">{dimension.rationale}</p>}
                        </>
                      }
                    >
                      <span className="cursor-help text-fg-2 underline decoration-line decoration-dotted underline-offset-[3px]">
                        {dimension.label}
                      </span>
                    </Tooltip>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </Card>

        <div className="grid content-start gap-4">
          <ScoreFactsCard facts={model.scoreFacts} />
          <BandScaleCard facts={model.scoreFacts} />
        </div>
      </div>
    </Section>
  );
}

function DimensionRow({ dimension }: { dimension: DimensionScore }) {
  const score = dimension.score ?? 0;
  const band = dimension.band ?? 'unsupported';
  const tone = BAND_TONE[band];

  return (
    <li className="py-3.5 first:pt-0 last:pb-0">
      <div className="flex items-start gap-4">
        <div className="min-w-0 flex-1">
          <Tooltip
            width={340}
            content={
              <>
                <strong className="text-fg">{dimension.label}</strong>
                <p className="mt-1 italic">{dimension.question}</p>
                <p className="mt-2">{dimension.rationale}</p>
                <p className="mt-2 border-t border-line pt-2 text-2xs text-fg-3">
                  Informed by {dimension.claims_considered}{' '}
                  {dimension.claims_considered === 1 ? 'claim' : 'claims'} · {dimension.confidence_band}{' '}
                  confidence ({dimension.confidence.toFixed(2)})
                </p>
              </>
            }
          >
            <span className="cursor-help text-[13.5px] font-medium text-fg underline decoration-line decoration-dotted underline-offset-[3px]">
              {dimension.label}
            </span>
          </Tooltip>

          <div className="mt-2 flex items-center gap-3">
            <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-subtle">
              <div
                className={cx('h-full rounded-full', toneClasses.fill[tone])}
                style={{ width: `${Math.max(0, Math.min(100, score))}%` }}
              />
            </div>
            <span className="num w-7 shrink-0 text-right text-[13px] font-semibold text-fg">
              {score.toFixed(0)}
            </span>
            <Badge tone={tone} className="w-[74px] justify-center">
              {BAND_LABEL[band]}
            </Badge>
          </div>

          <p className="mt-2 text-[13px] leading-relaxed text-fg-2">{dimension.rationale}</p>

          {(dimension.negative_drivers.length > 0 || dimension.positive_drivers.length > 0) && (
            <ul className="mt-2 space-y-1">
              {dimension.negative_drivers.slice(0, 2).map((driver, index) => (
                <li key={`neg-${index}`} className="flex gap-1.5 text-2xs leading-relaxed text-crit">
                  <span aria-hidden>↓</span>
                  <span>{driver.reason}</span>
                </li>
              ))}
              {dimension.positive_drivers.slice(0, 2).map((driver, index) => (
                <li key={`pos-${index}`} className="flex gap-1.5 text-2xs leading-relaxed text-pos">
                  <span aria-hidden>↑</span>
                  <span>{driver.reason}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </li>
  );
}

function ScoreFactsCard({ facts }: { facts: ScoreFacts }) {
  const entries: Array<[string, string]> = [];
  if (facts.claimsScored !== null) entries.push(['Claims scored', String(facts.claimsScored)]);
  if (facts.claimsExcluded !== null)
    entries.push(['Excluded by type', String(facts.claimsExcluded)]);
  if (facts.corroborated !== null) entries.push(['Corroborated', String(facts.corroborated)]);
  if (facts.unchecked !== null) entries.push(['Unchecked', String(facts.unchecked)]);
  if (facts.contradicted !== null) entries.push(['Contradicted', String(facts.contradicted)]);
  if (facts.anchor !== null) entries.push(['No-information anchor', facts.anchor.toFixed(0)]);
  if (facts.informationRatio !== null)
    entries.push(['Information ratio', facts.informationRatio.toFixed(2)]);
  if (facts.pageCoverage !== null)
    entries.push(['Page coverage', `${Math.round(facts.pageCoverage * 100)}%`]);

  return (
    <Card className="p-5">
      <h3 className="label">How the number was built</h3>
      <dl className="mt-3 space-y-2">
        {entries.map(([label, value]) => (
          <div key={label} className="flex items-baseline justify-between gap-3 text-[13px]">
            <dt className="text-fg-2">{label}</dt>
            <dd className="num font-medium text-fg">{value}</dd>
          </div>
        ))}
      </dl>
      {facts.methodology && (
        <p className="mt-3 border-t border-line pt-3 text-2xs leading-relaxed text-fg-3">
          {facts.methodology}
        </p>
      )}
    </Card>
  );
}

function BandScaleCard({ facts }: { facts: ScoreFacts }) {
  if (facts.bandThresholds.length === 0) return null;
  return (
    <Card className="p-5">
      <h3 className="label">Band scale</h3>
      <ul className="mt-3 space-y-1.5">
        {facts.bandThresholds.map((threshold) => (
          <li
            key={threshold.band}
            className="flex items-center justify-between gap-3 text-[13px]"
          >
            <span className="flex items-center gap-2">
              <span
                aria-hidden
                className={cx('h-2 w-2 rounded-sm', toneClasses.fill[BAND_TONE[threshold.band]])}
              />
              <span className="text-fg-2">{BAND_LABEL[threshold.band]}</span>
            </span>
            <span className="num text-fg-3">{threshold.from.toFixed(0)}+</span>
          </li>
        ))}
      </ul>
    </Card>
  );
}
