import { describe, expect, it } from 'vitest';

import { buildReportModel, evidenceStateOf, EVIDENCE_STATE } from './report-model';
import type { ReportInput } from './report-model';
import type { Assessment, Claim, CorroborationStatus, Report, Risk, RunDetail } from './types';

/* --------------------------------------------------------------- fixtures --- */

function assessment(overrides: Partial<Assessment> = {}): Assessment {
  return {
    credibility_score: 70,
    credibility_band: 'moderate',
    corroboration_status: 'corroborated',
    corroboration_rationale: '',
    is_scorable: true,
    score_explanation: '',
    verification_status: null,
    verification_source: null,
    verification_detail: null,
    verification_identifiers: [],
    confidence: 0.6,
    supporting_count: 0,
    contradicting_count: 0,
    neutral_count: 0,
    evidence_quality: 0,
    consistency: 0,
    novelty: 0,
    verdict: '',
    key_uncertainties: [],
    score_breakdown: {},
    ...overrides,
  };
}

function claim(id: string, overrides: Partial<Claim> = {}): Claim {
  return {
    id,
    statement: `Statement ${id}`,
    verbatim_quote: `Quote ${id}`,
    page_number: 1,
    from_visual: false,
    claim_type: 'regulatory_approval',
    category: 'regulatory',
    claimed_evidence_tier: 'none_stated',
    quantitative: [],
    is_scientific: true,
    is_falsifiable: true,
    hedging_language: false,
    importance: 0.5,
    is_thesis_critical: false,
    extraction_confidence: 0.9,
    quote_verification: 'exact',
    quote_match_score: 1,
    needs_human_review: false,
    review_reasons: [],
    entities: [],
    assessment: assessment(),
    ...overrides,
  };
}

function risk(id: string, overrides: Partial<Risk> = {}): Risk {
  return {
    id,
    claim_id: null,
    category: 'scientific',
    severity: 'medium',
    title: `Risk ${id}`,
    description: 'First sentence. Second sentence.',
    basis: '',
    evidence_ids: [],
    source_pages: [],
    is_rule_based: false,
    ...overrides,
  };
}

function run(overrides: Partial<RunDetail> = {}): RunDetail {
  return {
    id: 'run_1',
    document_id: 'doc_1',
    status: 'succeeded',
    current_stage: 'report',
    progress: 1,
    started_at: null,
    finished_at: null,
    duration_ms: 1000,
    error_code: null,
    error_message: null,
    pipeline_version: '1.0.0',
    created_at: '2026-01-01 00:00:00',
    document: {
      id: 'doc_1',
      filename: 'deck.pdf',
      content_hash: 'x',
      size_bytes: 10,
      page_count: 25,
      is_parsed: true,
      requires_ocr: false,
      notes: null,
      pdf_metadata: {},
      created_at: '2026-01-01 00:00:00',
    },
    stages: [],
    counts: {},
    metrics: {
      stages: { retrieval: { records_seen: 82, by_source: { pubmed: 60, europe_pmc: 2 } } },
    },
    config: {},
    profile: null,
    degraded: false,
    ...overrides,
  };
}

function report(overrides: Partial<Report> = {}): Report {
  return {
    id: 'rpt_1',
    run_id: 'run_1',
    title: 'Memo',
    executive_summary: 'Summary.',
    sections: [],
    overall_score: 70,
    overall_band: 'moderate',
    confidence: 0.6,
    recommendation: 'Advance.',
    score_breakdown: { no_information_anchor: 58, claims_scored: 2, unchecked_claims: 1 },
    scorecard: {},
    scientific_assessment: {},
    ic_recommendation: 'advance_with_conditions',
    citations: [],
    limitations: [],
    markdown: '',
    created_at: '2026-01-01 00:00:00',
    ...overrides,
  };
}

function input(overrides: Partial<ReportInput> = {}): ReportInput {
  return {
    run: run(),
    report: report(),
    claims: [],
    questions: [],
    risks: [],
    entities: [],
    evidence: [],
    evidenceByClaim: {},
    ...overrides,
  };
}

/* ------------------------------------------------------------------ tests --- */

describe('evidenceStateOf', () => {
  const cases: Array<[CorroborationStatus, string]> = [
    ['corroborated', 'verified'],
    ['partially_corroborated', 'partly_verified'],
    ['plausible_unverified', 'plausible'],
    ['not_independently_verified', 'unverified'],
    ['insufficient_evidence', 'no_evidence'],
    ['contradicted', 'contradicted'],
    ['disputed', 'disputed'],
    ['not_assessable', 'not_assessable'],
  ];

  it.each(cases)('maps %s to %s', (status, expected) => {
    expect(evidenceStateOf(claim('c', { assessment: assessment({ corroboration_status: status }) })))
      .toBe(expected);
  });

  it('treats a missing assessment as not assessed, not as a failure', () => {
    expect(evidenceStateOf(claim('c', { assessment: null }))).toBe('unassessed');
  });

  it('never buckets "could not check" with "the evidence disagrees"', () => {
    // The distinction the whole system is built around.
    for (const state of ['unverified', 'no_evidence', 'plausible'] as const) {
      expect(EVIDENCE_STATE[state].bucket).toBe('unverified');
    }
    for (const state of ['contradicted', 'disputed'] as const) {
      expect(EVIDENCE_STATE[state].bucket).toBe('contradicted');
    }
  });
});

describe('buildReportModel — score effect', () => {
  const anchoredAt58 = (score: number, status: CorroborationStatus = 'corroborated') =>
    buildReportModel(
      input({
        claims: [
          claim('c1', {
            assessment: assessment({ credibility_score: score, corroboration_status: status }),
          }),
        ],
      }),
    ).claims[0]!;

  it('reads a score well above the anchor as lifting', () => {
    const row = anchoredAt58(82);
    expect(row.effect).toBe('lifts');
    expect(row.effectDelta).toBe(24);
  });

  it('reads a score well below the anchor as dragging', () => {
    expect(anchoredAt58(29).effect).toBe('drags');
  });

  it('reads a score at the anchor as neutral', () => {
    expect(anchoredAt58(59).effect).toBe('neutral');
  });

  it('always treats a contradicted claim as dragging, whatever its score', () => {
    expect(anchoredAt58(90, 'contradicted').effect).toBe('drags');
  });

  it('excludes an unscorable claim rather than scoring it zero', () => {
    const model = buildReportModel(
      input({
        claims: [claim('c1', { assessment: assessment({ is_scorable: false }) })],
      }),
    );
    expect(model.claims[0]!.effect).toBe('excluded');
    expect(model.claims[0]!.score).toBeNull();
    expect(model.claims[0]!.effectDelta).toBeNull();
  });
});

describe('buildReportModel — headline statistics', () => {
  const model = buildReportModel(
    input({
      claims: [
        claim('a', { assessment: assessment({ corroboration_status: 'corroborated' }) }),
        claim('b', { assessment: assessment({ corroboration_status: 'partially_corroborated' }) }),
        claim('c', {
          assessment: assessment({ corroboration_status: 'not_independently_verified' }),
        }),
        claim('d', { assessment: assessment({ corroboration_status: 'insufficient_evidence' }) }),
        claim('e', { assessment: assessment({ corroboration_status: 'contradicted' }) }),
        claim('f', { assessment: assessment({ corroboration_status: 'not_assessable' }) }),
      ],
      risks: [risk('r1', { severity: 'critical' }), risk('r2', { severity: 'low' })],
    }),
  );

  const stat = (key: string) => model.stats.find((entry) => entry.key === key)?.value;

  it('counts every extracted claim', () => {
    expect(stat('claims')).toBe('6');
  });

  it('counts corroborated and partly corroborated as verified', () => {
    expect(stat('verified')).toBe('2');
  });

  it('counts unchecked claims separately from contradicted ones', () => {
    expect(stat('unverified')).toBe('2');
    expect(stat('contradicted')).toBe('1');
  });

  it('reports records screened from the retrieval metrics', () => {
    expect(stat('sources')).toBe('82');
  });

  it('counts critical and high findings as critical diligence items', () => {
    expect(stat('critical')).toBe('1');
  });

  it('builds a distribution that accounts for every claim', () => {
    const total = model.evidenceStateSlices.reduce((sum, slice) => sum + slice.value, 0);
    expect(total).toBe(model.claims.length);
  });
});

describe('buildReportModel — traceability', () => {
  it('links a risk back to the claim it came from', () => {
    const model = buildReportModel(
      input({
        claims: [claim('c1')],
        risks: [risk('r1', { claim_id: 'c1', severity: 'high' })],
      }),
    );
    expect(model.risks[0]!.claim?.id).toBe('c1');
    expect(model.claims[0]!.riskSeverity).toBe('high');
  });

  it('reports the most severe linked finding on a claim', () => {
    const model = buildReportModel(
      input({
        claims: [claim('c1')],
        risks: [
          risk('r1', { claim_id: 'c1', severity: 'low' }),
          risk('r2', { claim_id: 'c1', severity: 'critical' }),
        ],
      }),
    );
    expect(model.claims[0]!.riskSeverity).toBe('critical');
  });

  it('keeps a rule-based risk with no claim rather than dropping it', () => {
    const model = buildReportModel(input({ risks: [risk('r1', { is_rule_based: true })] }));
    expect(model.risks).toHaveLength(1);
    expect(model.risks[0]!.claim).toBeNull();
  });

  it('indexes citations by ref and resolves them to claims', () => {
    const model = buildReportModel(
      input({
        claims: [claim('c1')],
        report: report({
          citations: [{ ref: 'C1', kind: 'claim', claim_id: 'c1', statement: 'S', page_number: 9 }],
        }),
      }),
    );
    const entry = model.citationsByRef.get('C1');
    expect(entry?.claimId).toBe('c1');
    expect(entry?.pageNumber).toBe(9);
    expect(model.claimsById.get(entry!.claimId!)).toBeDefined();
  });

  it('discards a citation with no ref instead of indexing it as undefined', () => {
    const model = buildReportModel(
      input({ report: report({ citations: [{ kind: 'claim', statement: 'S' }] }) }),
    );
    expect(model.citations).toHaveLength(0);
  });
});

describe('buildReportModel — search index', () => {
  it('makes quotes and entities searchable, not just the statement', () => {
    const model = buildReportModel(
      input({
        claims: [
          claim('c1', {
            statement: 'Approved for adults',
            verbatim_quote: 'mRESVIA approval',
            entities: [{ id: 'e1', entity_type: 'product', name: 'mRESVIA', canonical_name: null }],
          }),
        ],
      }),
    );
    expect(model.claims[0]!.haystack).toContain('mresvia');
    expect(model.claims[0]!.haystack).toContain('approved for adults');
  });
});
