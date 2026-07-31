/**
 * Regression: one failing secondary endpoint must not lose the report.
 *
 * Production symptom was "The report could not be loaded" whenever
 * `/runs/{id}/evidence` threw, because the bundle was a single `Promise.all`
 * and any one rejection discarded six successful responses.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ApiRequestError } from './api';

const getRun = vi.fn();
const getReport = vi.fn();
const listClaims = vi.fn();
const listQuestions = vi.fn();
const listRisks = vi.fn();
const listEntities = vi.fn();
const listEvidence = vi.fn();
const getClaim = vi.fn();

vi.mock('./api', async () => {
  const actual = await vi.importActual<typeof import('./api')>('./api');
  return {
    ...actual,
    api: {
      getRun: (...a: unknown[]) => getRun(...a),
      getReport: (...a: unknown[]) => getReport(...a),
      listClaims: (...a: unknown[]) => listClaims(...a),
      listQuestions: (...a: unknown[]) => listQuestions(...a),
      listRisks: (...a: unknown[]) => listRisks(...a),
      listEntities: (...a: unknown[]) => listEntities(...a),
      listEvidence: (...a: unknown[]) => listEvidence(...a),
      getClaim: (...a: unknown[]) => getClaim(...a),
    },
  };
});

const { loadReportBundle } = await import('./run-data');

const RUN = { id: 'run_1', status: 'succeeded', stages: [], counts: {} };
const REPORT = { id: 'rpt_1', title: 'Memo', sections: [] };

beforeEach(() => {
  vi.spyOn(console, 'warn').mockImplementation(() => {});
  getRun.mockResolvedValue(RUN);
  getReport.mockResolvedValue(REPORT);
  listClaims.mockResolvedValue({ items: [] });
  listQuestions.mockResolvedValue([]);
  listRisks.mockResolvedValue([]);
  listEntities.mockResolvedValue([]);
  listEvidence.mockResolvedValue([]);
});

afterEach(() => {
  vi.clearAllMocks();
  vi.restoreAllMocks();
});

describe('loadReportBundle', () => {
  it('returns the full bundle when every endpoint works', async () => {
    const bundle = await loadReportBundle('run_1');
    expect(bundle).not.toBeNull();
    expect(bundle!.degraded).toEqual([]);
  });

  it('still renders the report when /evidence fails', async () => {
    listEvidence.mockRejectedValue(new ApiRequestError(500, 'db_error', 'json DISTINCT blew up'));

    const bundle = await loadReportBundle('run_1');

    expect(bundle).not.toBeNull();
    expect(bundle!.report).toEqual(REPORT);
    expect(bundle!.evidence).toEqual([]);
    expect(bundle!.degraded).toEqual(['evidence']);
  });

  it('keeps claims when only evidence fails', async () => {
    listClaims.mockResolvedValue({ items: [{ id: 'clm_1' }] });
    listEvidence.mockRejectedValue(new ApiRequestError(500, 'db_error', 'boom'));

    const bundle = await loadReportBundle('run_1');

    expect(bundle!.claims).toHaveLength(1);
    expect(bundle!.degraded).toEqual(['evidence']);
  });

  it('retries a failing secondary once before giving up', async () => {
    listEvidence
      .mockRejectedValueOnce(new ApiRequestError(504, 'timeout', 'slow'))
      .mockResolvedValueOnce([{ id: 'ev_1' }]);

    const bundle = await loadReportBundle('run_1');

    expect(listEvidence).toHaveBeenCalledTimes(2);
    expect(bundle!.evidence).toHaveLength(1);
    expect(bundle!.degraded).toEqual([]);
  });

  it('survives every secondary failing at once', async () => {
    const boom = () => Promise.reject(new ApiRequestError(500, 'x', 'down'));
    listClaims.mockImplementation(boom);
    listQuestions.mockImplementation(boom);
    listRisks.mockImplementation(boom);
    listEntities.mockImplementation(boom);
    listEvidence.mockImplementation(boom);

    const bundle = await loadReportBundle('run_1');

    expect(bundle).not.toBeNull();
    expect(bundle!.report).toEqual(REPORT);
    // The loads race, so completion order is not meaningful; membership is.
    expect([...bundle!.degraded!].sort()).toEqual(
      ['claims', 'entities', 'evidence', 'questions', 'risks'].sort(),
    );
  });

  it('returns null when the run produced no report', async () => {
    getReport.mockRejectedValue(new ApiRequestError(404, 'not_found', 'no memo'));
    expect(await loadReportBundle('run_1')).toBeNull();
  });

  it('propagates a failure of the run record itself', async () => {
    getRun.mockRejectedValue(new ApiRequestError(503, 'network_error', 'unreachable'));
    await expect(loadReportBundle('run_1')).rejects.toThrow();
  });
});
