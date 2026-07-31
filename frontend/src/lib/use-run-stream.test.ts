/**
 * Regression: a poll must actually update the displayed stage.
 *
 * Production symptom was a UI reading "Page Understanding" for a whole run
 * while `/runs/{id}` reported `current_stage="report"`, `progress=0.95`.
 * Polling succeeded; the state update discarded the response.
 *
 * The merge rule is pure, so it is tested directly rather than through a
 * rendered hook -- the defect was in the rule, not in the wiring.
 */

import { describe, expect, it } from 'vitest';

import { mergeProgress } from './use-run-stream';
import type { ProgressEvent, RunStatus } from './types';

function snapshot(stage: string, progress: number, status: RunStatus = 'running'): ProgressEvent {
  return {
    run_id: 'run_1',
    status,
    current_stage: stage,
    progress,
    error_code: null,
    error_message: null,
    stages: [],
    counts: {},
  };
}

describe('mergeProgress', () => {
  it('adopts a newer stage while the run stays running', () => {
    // The exact production case: both snapshots say "running", so the old
    // status-equality guard kept the stale one for the entire analysis.
    const current = snapshot('page_understanding', 0.24);
    const fresh = snapshot('report', 0.95);

    expect(mergeProgress(current, fresh).current_stage).toBe('report');
    expect(mergeProgress(current, fresh).progress).toBe(0.95);
  });

  it('advances through every stage of a run', () => {
    const stages: Array<[string, number]> = [
      ['parse', 0.06],
      ['page_understanding', 0.24],
      ['claims', 0.52],
      ['retrieval', 0.68],
      ['report', 0.95],
    ];

    let current: ProgressEvent | null = null;
    for (const [stage, value] of stages) {
      current = mergeProgress(current, snapshot(stage, value));
      expect(current.current_stage).toBe(stage);
    }
    expect(current!.progress).toBe(0.95);
  });

  it('takes the first snapshot when there is nothing to merge with', () => {
    expect(mergeProgress(null, snapshot('parse', 0.06)).current_stage).toBe('parse');
  });

  it('refuses to move progress backwards mid-run', () => {
    // A poll landing behind a live stream frame must not rewind the bar.
    const current = snapshot('report', 0.95);
    const stale = snapshot('claims', 0.52);
    expect(mergeProgress(current, stale)).toBe(current);
  });

  it('always adopts a terminal snapshot, even a lower one', () => {
    // A failed run legitimately reports less progress than it had reached.
    const current = snapshot('report', 0.95);
    const failed = snapshot('report', 0.4, 'failed');
    expect(mergeProgress(current, failed).status).toBe('failed');
  });

  it('adopts a terminal success so the report can load', () => {
    const current = snapshot('report', 0.95);
    const done = snapshot('report', 1, 'succeeded');
    expect(mergeProgress(current, done).status).toBe('succeeded');
  });

  it('accepts an equal-progress snapshot so stage detail stays current', () => {
    // Progress is coarse; the stage can change without the number moving.
    const current = snapshot('assessment', 0.9);
    const fresh = snapshot('questions', 0.9);
    expect(mergeProgress(current, fresh).current_stage).toBe('questions');
  });
});
