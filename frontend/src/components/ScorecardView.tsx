import { Chip, humanise } from '@/components/ui';
import type { CorroborationStatus, DimensionScore, ICRecommendation, Scorecard } from '@/lib/types';

const BAND_BAR: Record<string, string> = {
  strong: 'bg-band-strong',
  moderate: 'bg-band-moderate',
  limited: 'bg-band-limited',
  weak: 'bg-band-weak',
  unsupported: 'bg-band-unsupported',
};

const RECOMMENDATION_STYLES: Record<ICRecommendation, string> = {
  advance: 'border-band-strong/40 bg-band-strong/10 text-band-strong',
  advance_with_conditions: 'border-band-moderate/40 bg-band-moderate/10 text-band-moderate',
  further_diligence_required: 'border-band-limited/40 bg-band-limited/10 text-band-limited',
  significant_concerns: 'border-band-weak/40 bg-band-weak/10 text-band-weak',
  do_not_advance: 'border-band-unsupported/40 bg-band-unsupported/10 text-band-unsupported',
};

/**
 * The ten-dimension IC scorecard.
 *
 * Replaces the single opaque credibility number. A dimension with no informing
 * claims renders as *not assessed* rather than zero — the distinction between
 * "we found a problem" and "the deck said nothing about this" is the whole
 * point of the view.
 */
export function ScorecardView({ scorecard }: { scorecard: Scorecard }) {
  const assessed = scorecard.dimensions.filter((d) => d.assessed);
  const unassessed = scorecard.dimensions.filter((d) => !d.assessed);

  return (
    <div className="space-y-5">
      <div
        className={`rounded-lg border px-4 py-3 ${RECOMMENDATION_STYLES[scorecard.recommendation]}`}
      >
        <div className="text-[11px] font-semibold uppercase tracking-wider opacity-80">
          Scientific diligence recommendation
        </div>
        <div className="mt-1 text-lg font-semibold">
          {humanise(scorecard.recommendation)}
        </div>
        <p className="mt-1.5 text-sm text-ink-700 dark:text-ink-300">
          {scorecard.recommendation_rationale}
        </p>
        <div className="mt-2.5 flex flex-wrap gap-1.5">
          <Chip tone="accent">{humanise(scorecard.archetype)}</Chip>
          <Chip>confidence {scorecard.overall_confidence.toFixed(2)}</Chip>
          <Chip>{assessed.length} of 10 dimensions assessed</Chip>
        </div>
      </div>

      <div className="card">
        <h3 className="text-base font-semibold">Scorecard</h3>
        <p className="mt-1 text-sm text-ink-500">
          Computed deterministically from the claim-level analysis. Weighting is set by company
          archetype, because a platform company and a single-asset company do not carry the same
          risks.
        </p>

        <ul className="mt-4 space-y-4">
          {assessed.map((dimension) => (
            <DimensionRow key={dimension.dimension} dimension={dimension} />
          ))}
        </ul>

        {unassessed.length > 0 && (
          <div className="mt-6 border-t border-ink-200 pt-4 dark:border-ink-800">
            <h4 className="label">Not assessed</h4>
            <p className="mt-1 text-xs text-ink-500">
              No claims in the deck bore on these. That is a gap in what was disclosed, not a
              negative finding.
            </p>
            <ul className="mt-2 space-y-1.5">
              {unassessed.map((dimension) => (
                <li key={dimension.dimension} className="text-sm">
                  <span className="font-medium">{dimension.label}</span>
                  <span className="text-ink-500"> — {dimension.rationale}</span>
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </div>
  );
}

function DimensionRow({ dimension }: { dimension: DimensionScore }) {
  const score = dimension.score ?? 0;
  const bar = BAND_BAR[dimension.band ?? 'unsupported'] ?? 'bg-ink-400';

  return (
    <li>
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <span className="font-medium">{dimension.label}</span>
        <span className="flex items-center gap-2 text-sm">
          <span className="font-mono tabular-nums">{score.toFixed(0)}</span>
          <span className="text-xs text-ink-500">
            {humanise(dimension.band ?? '')} · {dimension.confidence_band} confidence
          </span>
        </span>
      </div>

      <div className="mt-1.5 h-1.5 w-full overflow-hidden rounded-full bg-ink-200 dark:bg-ink-800">
        <div className={`h-full rounded-full ${bar}`} style={{ width: `${score}%` }} />
      </div>

      <p className="mt-1.5 text-xs text-ink-500">{dimension.question}</p>
      <p className="mt-1 text-sm text-ink-600 dark:text-ink-400">{dimension.rationale}</p>

      {dimension.negative_drivers.length > 0 && (
        <ul className="mt-1.5 space-y-0.5">
          {dimension.negative_drivers.slice(0, 3).map((driver, index) => (
            <li key={index} className="text-xs text-band-weak">
              ↓ {driver.reason}
            </li>
          ))}
        </ul>
      )}
      {dimension.positive_drivers.length > 0 && (
        <ul className="mt-1 space-y-0.5">
          {dimension.positive_drivers.slice(0, 2).map((driver, index) => (
            <li key={index} className="text-xs text-band-strong">
              ↑ {driver.reason}
            </li>
          ))}
        </ul>
      )}
    </li>
  );
}

/* --------------------------------------------------------- corroboration --- */

const CORROBORATION_STYLES: Record<CorroborationStatus, string> = {
  corroborated: 'border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400',
  partially_corroborated: 'border-emerald-500/20 bg-emerald-500/5 text-emerald-700 dark:text-emerald-400',
  plausible_unverified: 'border-sky-500/30 bg-sky-500/10 text-sky-700 dark:text-sky-400',
  // Deliberately neutral, not warning-coloured: these mean "we could not
  // check", and colouring them as problems is the visual form of the bug
  // this system was corrected for.
  insufficient_evidence: 'border-ink-400/30 bg-ink-400/10 text-ink-600 dark:text-ink-400',
  not_independently_verified: 'border-ink-400/30 bg-ink-400/10 text-ink-600 dark:text-ink-400',
  contradicted: 'border-red-500/40 bg-red-500/10 text-red-700 dark:text-red-400',
  disputed: 'border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-400',
  not_assessable: 'border-ink-300/30 bg-ink-300/10 text-ink-500',
};

const CORROBORATION_LABELS: Record<CorroborationStatus, string> = {
  corroborated: 'Corroborated',
  partially_corroborated: 'Partly corroborated',
  plausible_unverified: 'Plausible, unverified',
  insufficient_evidence: 'No evidence found',
  not_independently_verified: 'Not independently verified',
  contradicted: 'Contradicted',
  disputed: 'Disputed',
  not_assessable: 'Not assessable',
};

export function CorroborationChip({ status }: { status: CorroborationStatus }) {
  return (
    <span className={`chip ${CORROBORATION_STYLES[status]}`} title={CORROBORATION_TOOLTIPS[status]}>
      {CORROBORATION_LABELS[status]}
    </span>
  );
}

const CORROBORATION_TOOLTIPS: Record<CorroborationStatus, string> = {
  corroborated: 'Independent evidence of a grade capable of settling this claim confirms it.',
  partially_corroborated: 'The direction is confirmed but a material specific is not.',
  plausible_unverified:
    'Consistent with what the retrieved literature establishes, but not individually confirmed.',
  insufficient_evidence:
    'The search ran and returned nothing on point. This says nothing about whether the claim is true.',
  not_independently_verified:
    'Only a regulator, a registry or the company could confirm this, and that source was not available or does not cover it.',
  contradicted: 'Retrieved evidence genuinely disagrees with this claim.',
  disputed: 'The evidence points both ways with comparable weight.',
  not_assessable:
    'Not a factual claim about the present world (vision, guidance, plan or marketing); excluded from scoring.',
};
