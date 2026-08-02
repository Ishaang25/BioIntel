'use client';

import { firstSentence } from '@/lib/format';
import {
  BAND_LABEL,
  BAND_TONE,
  recommendationLabel,
  recommendationTone,
} from '@/lib/report-model';
import { Badge, Card, Meter, ScoreArc, cx, toneClasses } from '@/components/ui/primitives';
import { InfoHint } from '@/components/ui/Tooltip';
import { useReport } from '../context';

/**
 * The first screen, and the only one that has to work with no explanation.
 *
 * It answers four questions in the order an investment committee asks them:
 * what is the score, what should we do, why, and what could go wrong. Anything
 * that is not one of those four — the metric grid, the run provenance, the
 * scorecard mathematics — moved below or behind a click, because on the old
 * single-scroll layout they competed with the verdict for the same first
 * screenful and the reader had to work out which number was the answer.
 */
export function VerdictSection() {
  const { model, goToSection, openClaim } = useReport();
  const tone = recommendationTone(model.recommendation);
  const why = model.recommendationRationale || firstSentence(model.executiveSummary, 260);
  const topRisks = model.keyRisks.slice(0, 3);

  return (
    <section id="verdict" aria-labelledby="verdict-heading" className="scroll-anchor">
      <h2 id="verdict-heading" className="sr-only">
        Verdict
      </h2>

      <Card className="overflow-hidden">
        <div className="flex flex-col gap-6 p-6 sm:flex-row sm:items-center sm:gap-8">
          <div className="flex shrink-0 flex-col items-center">
            <ScoreArc score={model.score} band={model.band} size={152} />
            <div
              className={cx(
                'mt-1 text-[13px] font-medium',
                toneClasses.text[BAND_TONE[model.band]],
              )}
            >
              {BAND_LABEL[model.band]}
            </div>
            <div className="mt-0.5 flex items-center gap-1 text-2xs text-fg-3">
              scientific credibility
              <InfoHint
                content={
                  <>
                    <strong className="text-fg">What this measures</strong>
                    <p className="mt-1">
                      How well the deck&rsquo;s scientific claims stand up against the independent
                      record. It is not a valuation, a market view, or a prediction of returns.
                    </p>
                  </>
                }
              />
            </div>
          </div>

          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2">
              <span className="label">Recommendation</span>
              {model.archetype && <Badge>{model.archetype.replace(/_/g, ' ')}</Badge>}
            </div>
            <p
              className={cx(
                'mt-1.5 text-2xl font-semibold tracking-[-0.02em]',
                toneClasses.text[tone],
              )}
            >
              {recommendationLabel(model.recommendation)}
            </p>
            {why && (
              <p className="mt-3 max-w-prose text-[14px] leading-relaxed text-fg-2">{why}</p>
            )}

            <div className="mt-5 max-w-xs">
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
        </div>

        <div className="border-t border-line bg-subtle/40 px-6 py-5">
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <h3 className="text-[13.5px] font-semibold text-fg">
              {topRisks.length > 0 ? 'Biggest risks' : 'Risks'}
            </h3>
            {model.risks.length > topRisks.length && (
              <button
                type="button"
                className="text-[13px] text-fg-2 underline decoration-line-strong underline-offset-2 transition-colors hover:text-fg"
                onClick={() => goToSection('risks')}
              >
                All {model.risks.length} findings
              </button>
            )}
          </div>

          {topRisks.length === 0 ? (
            <p className="mt-2 text-[13px] text-fg-2">
              No critical or high-severity findings were raised. That is an absence of red flags,
              not a positive result — the Evidence view shows how much could be checked.
            </p>
          ) : (
            <ul className="mt-3 space-y-2.5">
              {topRisks.map((risk) => (
                <li key={risk.id} className="flex gap-2.5">
                  <span
                    aria-hidden
                    className={cx(
                      'mt-[7px] h-1.5 w-1.5 shrink-0 rounded-full',
                      toneClasses.fill[risk.tone],
                    )}
                  />
                  <div className="min-w-0 flex-1">
                    <p className="text-[13.5px] font-medium leading-snug text-fg">{risk.text}</p>
                    {risk.detail && (
                      <p className="mt-0.5 text-[13px] leading-relaxed text-fg-2">
                        {firstSentence(risk.detail, 170)}
                      </p>
                    )}
                    {risk.claimId && (
                      <button
                        type="button"
                        className="mt-1 text-2xs text-fg-3 underline decoration-line-strong underline-offset-2 transition-colors hover:text-fg"
                        onClick={() => openClaim(risk.claimId!)}
                      >
                        trace to the claim
                      </button>
                    )}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>
      </Card>
    </section>
  );
}
