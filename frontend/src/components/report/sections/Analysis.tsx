'use client';

import { useState } from 'react';

import { humanise } from '@/lib/format';
import type { ConfidenceLevel, ReportSection } from '@/lib/types';
import type { Tone } from '@/lib/report-model';
import { Badge, Card, cx } from '@/components/ui/primitives';
import { useReport } from '../context';
import { Prose } from '../Prose';
import { Section } from './Section';

const CONFIDENCE_TONE: Record<ConfidenceLevel, Tone> = {
  high: 'pos',
  medium: 'info',
  low: 'warn',
};

/**
 * The analyst's narrative.
 *
 * Still the model's prose, but no longer presented as a wall of markdown: each
 * section is a card with its bottom line pulled out, its confidence stated, and
 * every reference marker turned into a control that opens the underlying claim.
 */
export function AnalysisSection() {
  const { model } = useReport();
  const [active, setActive] = useState<string | null>(null);

  if (model.sections.length === 0) return null;

  return (
    <Section
      id="analysis"
      title="Analysis"
      description="Nine sections of scientific assessment. Every bracketed reference resolves to the extracted claim it rests on — hover or click to see it."
      actions={
        <div className="hidden flex-wrap gap-1 xl:flex">
          {model.sections.map((section) => (
            <button
              key={section.id}
              type="button"
              onClick={() => {
                setActive(section.id);
                document
                  .getElementById(`analysis-${section.id}`)
                  ?.scrollIntoView({ block: 'start', behavior: 'smooth' });
              }}
              className={cx(
                'rounded-md px-2 py-1 text-2xs transition-colors',
                active === section.id
                  ? 'bg-subtle font-medium text-fg'
                  : 'text-fg-3 hover:bg-subtle hover:text-fg',
              )}
            >
              {section.heading}
            </button>
          ))}
        </div>
      }
    >
      <div className="space-y-4">
        {model.sections.map((section) => (
          <NarrativeCard key={section.id} section={section} />
        ))}
      </div>
    </Section>
  );
}

/**
 * Prose on the left at a readable measure, the section's judgement in a margin
 * rail on the right. A full-width paragraph at 1440px runs past 110 characters
 * a line, which no one reads carefully; the rail uses the space for the parts
 * an investment committee actually stops on.
 */
function NarrativeCard({ section }: { section: ReportSection }) {
  const confidence = section.confidence || null;

  return (
    <Card
      as="article"
      id={`analysis-${section.id}`}
      className="scroll-anchor grid gap-x-8 gap-y-5 p-6 xl:grid-cols-[minmax(0,72ch)_288px]"
    >
      <div className="min-w-0">
        <h3 className="mb-3 border-b border-line pb-3 text-[15px] font-semibold tracking-[-0.01em] text-fg">
          {section.heading}
        </h3>
        <Prose markdown={section.body_markdown} />
      </div>

      <aside className="space-y-4 xl:border-l xl:border-line xl:pl-8">
        {section.so_what && (
          <div className="rounded-lg border border-line border-l-2 border-l-fg bg-subtle px-3.5 py-3">
            <div className="label">So what</div>
            <p className="mt-1.5 text-[13px] font-medium leading-relaxed text-fg">
              {section.so_what}
            </p>
          </div>
        )}

        <dl className="space-y-3">
          {confidence && (
            <div>
              <dt className="label mb-1.5">Confidence</dt>
              <dd>
                <Badge tone={CONFIDENCE_TONE[confidence]}>{humanise(confidence)}</Badge>
                {section.confidence_reason && (
                  <p className="mt-1.5 text-2xs leading-relaxed text-fg-3">
                    {section.confidence_reason}
                  </p>
                )}
              </dd>
            </div>
          )}
          {section.basis && (
            <div>
              <dt className="label mb-1">Basis</dt>
              <dd className="text-2xs text-fg-3">{humanise(section.basis)}</dd>
            </div>
          )}
          {section.citations.length > 0 && (
            <div>
              <dt className="label mb-1">References</dt>
              <dd className="num text-2xs text-fg-3">
                {section.citations.length} cited in this section
              </dd>
            </div>
          )}
        </dl>
      </aside>
    </Card>
  );
}
