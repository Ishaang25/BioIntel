'use client';

import { formatDateTime, formatDuration } from '@/lib/format';
import {
  BAND_LABEL,
  BAND_TONE,
  recommendationLabel,
  recommendationTone,
} from '@/lib/report-model';
import {
  Badge,
  Card,
  Meter,
  MetricCard,
  ScoreArc,
  cx,
  toneClasses,
} from '@/components/ui/primitives';
import { InfoHint } from '@/components/ui/Tooltip';
import { useReport } from '../context';
import { Section } from './Section';

/**
 * The answer, above the fold.
 *
 * Score, recommendation and the six numbers a partner asks for before reading
 * anything — each of which is a control that jumps to the evidence behind it.
 */
export function OverviewSection() {
  const { model, goToSection } = useReport();
  const tone = recommendationTone(model.recommendation);

  return (
    <Section id="overview" title="Overview">
      <div className="grid gap-4 xl:grid-cols-[minmax(0,340px)_minmax(0,1fr)]">
        <Card className="flex items-center gap-5 p-5">
          <ScoreArc score={model.score} band={model.band} size={148} />
          <div className="min-w-0 flex-1">
            <div className="label">Scientific credibility</div>
            <div
              className={cx(
                'mt-1.5 text-lg font-semibold tracking-[-0.01em]',
                toneClasses.text[BAND_TONE[model.band]],
              )}
            >
              {BAND_LABEL[model.band]}
            </div>

            <div className="mt-4">
              <div className="flex items-center justify-between gap-2">
                <span className="label flex items-center gap-1">
                  Confidence
                  <InfoHint
                    content={
                      <>
                        <strong className="text-fg">How sure the method is</strong>
                        <p className="mt-1">
                          Driven by how much of the deck could be independently checked. Low
                          confidence means thin verification, not a weak company.
                        </p>
                      </>
                    }
                  />
                </span>
                <span className="num text-[13px] font-medium text-fg">
                  {(model.confidence * 100).toFixed(0)}%
                </span>
              </div>
              <Meter
                className="mt-1.5"
                value={model.confidence * 100}
                tone={model.confidence >= 0.7 ? 'pos' : model.confidence >= 0.45 ? 'info' : 'warn'}
                label="Confidence in this assessment"
              />
            </div>
          </div>
        </Card>

        <Card className="flex flex-col p-5">
          <div className="flex flex-wrap items-center gap-2">
            <span className="label">Recommendation</span>
            {model.archetype && <Badge>{model.archetype.replace(/_/g, ' ')}</Badge>}
          </div>
          <div
            className={cx(
              'mt-2 text-xl font-semibold tracking-[-0.015em]',
              toneClasses.text[tone],
            )}
          >
            {recommendationLabel(model.recommendation)}
          </div>
          {model.recommendationRationale && (
            <p className="mt-2.5 max-w-prose text-[13.5px] leading-relaxed text-fg-2">
              {model.recommendationRationale}
            </p>
          )}
          <div className="mt-auto flex flex-wrap gap-2 pt-4">
            <button
              type="button"
              className="btn btn-sm btn-secondary"
              onClick={() => goToSection('summary')}
            >
              Read the summary
            </button>
            <button
              type="button"
              className="btn btn-sm btn-secondary"
              onClick={() => goToSection('risks')}
            >
              {model.risks.length} findings
            </button>
            <button
              type="button"
              className="btn btn-sm btn-secondary"
              onClick={() => goToSection('questions')}
            >
              {model.questions.length} questions for management
            </button>
          </div>
        </Card>
      </div>

      <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6">
        {model.stats.map((stat) => (
          <MetricCard
            key={stat.key}
            label={stat.label}
            value={stat.value}
            hint={stat.hint}
            tone={stat.tone}
            onClick={stat.target ? () => goToSection(stat.target!) : undefined}
          />
        ))}
      </div>

      <dl className="mt-4 flex flex-wrap items-center gap-x-6 gap-y-2 rounded-card border border-line bg-subtle/60 px-4 py-3 text-[12.5px]">
        <MetaItem label="Report generated" value={formatDateTime(model.createdAt)} />
        <MetaItem label="Analysis duration" value={formatDuration(model.durationMs)} />
        <MetaItem label="Source" value={`${model.filename} · ${model.pageCount} pages`} />
        <MetaItem label="Pipeline" value={`v${model.pipelineVersion}`} />
      </dl>
    </Section>
  );
}

function MetaItem({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex min-w-0 items-baseline gap-2">
      <dt className="label shrink-0">{label}</dt>
      <dd className="num truncate text-fg-2">{value}</dd>
    </div>
  );
}
