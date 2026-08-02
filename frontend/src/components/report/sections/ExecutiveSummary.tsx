'use client';

import type { ReactNode } from 'react';

import { firstSentence } from '@/lib/format';
import type { Highlight } from '@/lib/report-model';
import { recommendationLabel, recommendationTone } from '@/lib/report-model';
import { Badge, Card, Meter, cx, toneClasses } from '@/components/ui/primitives';
import { useReport } from '../context';
import { Prose, TextBlock } from '../Prose';
import { Section } from './Section';

/**
 * The memo's front page, rebuilt as structure rather than prose.
 *
 * Each block answers one question — what is this, what works, what should we
 * do, how much should we trust it — so a reader can stop after any one of them
 * and still have something complete.
 *
 * The key risks used to sit here too, in a panel identical to the one on the
 * verdict card directly above. Two copies of the same three findings on one
 * screen reads as two different sets of findings; the risks now appear once,
 * at the top, with the full list one click away.
 */
export function ExecutiveSummarySection() {
  const { model, openClaim, goToSection } = useReport();

  return (
    <Section
      id="summary"
      title="Executive summary"
      description={model.title}
    >
      <div className="grid gap-4 lg:grid-cols-[minmax(0,1.55fr)_minmax(0,1fr)]">
        <Card className="p-6">
          <TextBlock text={model.executiveSummary} />
        </Card>

        <div className="grid gap-4 content-start">
          <HighlightPanel
            title="Key strengths"
            tone="pos"
            empty="No scorecard driver rose to the level of a headline strength."
            highlights={model.keyStrengths}
            onSelect={openClaim}
            footer={
              model.risks.length > 0 ? (
                <button
                  type="button"
                  className="btn btn-sm btn-ghost -ml-2.5"
                  onClick={() => goToSection('risks')}
                >
                  All {model.risks.length} findings →
                </button>
              ) : null
            }
          />
        </div>
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-[minmax(0,1.55fr)_minmax(0,1fr)]">
        <Card className="p-6">
          <div className="mb-3 flex flex-wrap items-center gap-2">
            <h3 className="label">Why this recommendation</h3>
            <Badge tone={recommendationTone(model.recommendation)}>
              {recommendationLabel(model.recommendation)}
            </Badge>
          </div>
          <Prose markdown={model.recommendationMarkdown} />
        </Card>

        <Card className="p-6">
          <h3 className="label">Why this confidence</h3>
          <div className="mt-3 flex items-baseline gap-3">
            <span className="num text-3xl font-semibold leading-none tracking-[-0.02em] text-fg">
              {(model.confidence * 100).toFixed(0)}
              <span className="text-base font-normal text-fg-3">%</span>
            </span>
            <span className="text-[13px] text-fg-2">confidence in this assessment</span>
          </div>
          <Meter
            className="mt-3"
            value={model.confidence * 100}
            tone={model.confidence >= 0.7 ? 'pos' : model.confidence >= 0.45 ? 'info' : 'warn'}
            label="Confidence"
          />
          <p className="mt-3.5 text-[13px] leading-relaxed text-fg-2">
            {model.confidenceNote ||
              'Confidence reflects how much of the deck could be checked against an independent record.'}
          </p>
          <p className="mt-2.5 border-t border-line pt-2.5 text-2xs leading-relaxed text-fg-3">
            Absence of corroboration never reduces the score — it only limits confidence. Only
            contradicting evidence moves the number down.
          </p>
        </Card>
      </div>
    </Section>
  );
}

function HighlightPanel({
  title,
  tone,
  highlights,
  empty,
  onSelect,
  footer,
}: {
  title: string;
  tone: 'pos' | 'crit';
  highlights: Highlight[];
  empty: string;
  onSelect: (claimId: string) => void;
  footer?: ReactNode;
}) {
  return (
    <Card className="p-5">
      <h3 className="label flex items-center gap-1.5">
        <span aria-hidden className={cx('h-2 w-2 rounded-sm', toneClasses.fill[tone])} />
        {title}
      </h3>
      {highlights.length === 0 ? (
        <p className="mt-2.5 text-[13px] text-fg-3">{empty}</p>
      ) : (
        <ul className="mt-3 space-y-2.5">
          {highlights.map((highlight) => (
            <li key={highlight.id} className="flex gap-2.5">
              <span
                aria-hidden
                className={cx(
                  'mt-[7px] h-1 w-1 shrink-0 rounded-full',
                  toneClasses.fill[highlight.tone],
                )}
              />
              <div className="min-w-0 flex-1">
                <p className="text-[13px] font-medium leading-snug text-fg">{highlight.text}</p>
                {highlight.detail && (
                  <p className="mt-1 text-2xs leading-relaxed text-fg-2">
                    {firstSentence(highlight.detail, 180)}
                  </p>
                )}
                <div className="mt-1.5 flex items-center gap-2 text-2xs text-fg-3">
                  <span className="truncate">{highlight.source}</span>
                  {highlight.claimId && (
                    <button
                      type="button"
                      className="shrink-0 underline decoration-line-strong underline-offset-2 hover:text-fg"
                      onClick={() => onSelect(highlight.claimId!)}
                    >
                      trace claim
                    </button>
                  )}
                </div>
              </div>
            </li>
          ))}
        </ul>
      )}
      {footer}
    </Card>
  );
}
