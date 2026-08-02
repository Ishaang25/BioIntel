/**
 * A run takes minutes. Whatever the UI says about the wait has to be true.
 */

import { describe, expect, it } from 'vitest';

import {
  STAGE_NARRATIVE,
  STAGE_ORDER,
  currentActivityLine,
  estimateRemainingMs,
  formatRemaining,
  stageLabel,
} from './run-progress';

describe('stage narrative', () => {
  it('covers every stage the pipeline reports', () => {
    for (const stage of STAGE_ORDER) {
      expect(STAGE_NARRATIVE[stage], `no narrative for "${stage}"`).toBeDefined();
    }
  });

  it('falls back to a readable label for an unknown stage', () => {
    expect(stageLabel('some_future_stage')).toBe('Some future stage');
  });
});

describe('currentActivityLine', () => {
  it('prefers the live sub-step from the backend', () => {
    expect(currentActivityLine('report', 'Writing the executive summary')).toBe(
      'Writing the executive summary',
    );
  });

  it('falls back to the stage description when there is no sub-step', () => {
    expect(currentActivityLine('retrieval', null)).toBe(STAGE_NARRATIVE.retrieval!.doing);
  });

  it('says nothing rather than guessing', () => {
    expect(currentActivityLine(null, null)).toBeNull();
    expect(currentActivityLine('not_a_stage', undefined)).toBeNull();
  });
});

describe('estimateRemainingMs', () => {
  it('extrapolates from the observed rate', () => {
    // Half done after two minutes: two minutes to go.
    expect(estimateRemainingMs(0.5, 120_000)).toBe(120_000);
  });

  it('shrinks as the run advances', () => {
    const early = estimateRemainingMs(0.25, 60_000)!;
    const later = estimateRemainingMs(0.75, 180_000)!;
    expect(later).toBeLessThan(early);
  });

  it.each([
    ['too early in the run', 0.01, 60_000],
    ['too little elapsed time to have a rate', 0.5, 5_000],
    ['already finished', 1, 600_000],
    ['an estimate too far out to be honest', 0.05, 300_000],
  ])('declines to guess: %s', (_why, progress, elapsed) => {
    expect(estimateRemainingMs(progress, elapsed)).toBeNull();
  });

  it('never returns a negative duration', () => {
    const value = estimateRemainingMs(0.999, 600_000);
    expect(value === null || value >= 0).toBe(true);
  });
});

describe('formatRemaining', () => {
  it.each([
    [20_000, 'under a minute remaining'],
    [65_000, 'about a minute remaining'],
    [200_000, 'about 3 minutes remaining'],
  ])('formats %ims as "%s"', (ms, expected) => {
    expect(formatRemaining(ms)).toBe(expected);
  });
});
