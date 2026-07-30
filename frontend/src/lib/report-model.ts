/**
 * The report view model.
 *
 * Everything the diligence workspace renders is derived here, once, from the
 * raw API payloads. Components receive finished values — no component reaches
 * back into `Record<string, unknown>` blobs or recomputes a bucket. That keeps
 * the presentation layer declarative and makes the derivations testable.
 */

import { humanise } from './format';
import type {
  Claim,
  CorroborationStatus,
  CredibilityBand,
  DimensionScore,
  Entity,
  Evidence,
  EvidenceLink,
  ICRecommendation,
  Question,
  Report,
  ReportSection,
  Risk,
  RiskSeverity,
  RunDetail,
  Scorecard,
} from './types';
import { hasScorecard } from './types';

/* -------------------------------------------------------------------- tone --- */

/**
 * The semantic colour scale, ordered from reassuring to alarming.
 *
 * Five steps rather than three, so severity can be read at a glance without a
 * legend: green, blue, amber, orange, red — plus a neutral for "no signal",
 * which is the state most of a cautious analysis lands in.
 */
export type Tone = 'pos' | 'info' | 'warn' | 'alert' | 'crit' | 'neutral';

/* ---------------------------------------------------------- evidence state --- */

/**
 * How well an extracted claim stood up to the outside record.
 *
 * The system's central distinction is preserved: *we could not check* is not
 * *the evidence disagrees*. Only `contradicted` and `disputed` are failures.
 */
export type EvidenceState =
  | 'verified'
  | 'partly_verified'
  | 'plausible'
  | 'unverified'
  | 'no_evidence'
  | 'contradicted'
  | 'disputed'
  | 'not_assessable'
  | 'unassessed';

export interface EvidenceStateMeta {
  label: string;
  short: string;
  tone: Tone;
  description: string;
  /** Roll-up bucket used by the headline statistics and the distribution chart. */
  bucket: 'verified' | 'unverified' | 'contradicted' | 'excluded';
}

export const EVIDENCE_STATE: Record<EvidenceState, EvidenceStateMeta> = {
  verified: {
    label: 'Verified',
    short: 'Verified',
    tone: 'pos',
    description:
      'Independent evidence of a grade capable of settling this claim confirms it.',
    bucket: 'verified',
  },
  partly_verified: {
    label: 'Partly verified',
    short: 'Partly',
    tone: 'pos',
    description: 'The direction of the claim is confirmed but a material specific is not.',
    bucket: 'verified',
  },
  plausible: {
    label: 'Plausible, unverified',
    short: 'Plausible',
    tone: 'info',
    description:
      'Consistent with what the retrieved literature establishes, but not individually confirmed.',
    bucket: 'unverified',
  },
  unverified: {
    label: 'Not independently verified',
    short: 'Unverified',
    tone: 'neutral',
    description:
      'Only a regulator, a registry or the company could confirm this, and that source was unavailable or does not cover it.',
    bucket: 'unverified',
  },
  no_evidence: {
    label: 'Insufficient evidence',
    short: 'No evidence',
    tone: 'neutral',
    description:
      'The search ran and returned nothing on point. This says nothing about whether the claim is true.',
    bucket: 'unverified',
  },
  contradicted: {
    label: 'Contradicted',
    short: 'Contradicted',
    tone: 'crit',
    description: 'Retrieved evidence genuinely disagrees with this claim.',
    bucket: 'contradicted',
  },
  disputed: {
    label: 'Disputed',
    short: 'Disputed',
    tone: 'warn',
    description: 'The evidence points both ways with comparable weight.',
    bucket: 'contradicted',
  },
  not_assessable: {
    label: 'Not assessable',
    short: 'Excluded',
    tone: 'neutral',
    description:
      'Not a factual claim about the present world — vision, guidance, plan or marketing. Excluded from scoring.',
    bucket: 'excluded',
  },
  unassessed: {
    label: 'Not assessed',
    short: 'Not assessed',
    tone: 'neutral',
    description: 'The assessment stage did not produce a judgement for this claim.',
    bucket: 'excluded',
  },
};

const CORROBORATION_TO_STATE: Record<CorroborationStatus, EvidenceState> = {
  corroborated: 'verified',
  partially_corroborated: 'partly_verified',
  plausible_unverified: 'plausible',
  not_independently_verified: 'unverified',
  insufficient_evidence: 'no_evidence',
  contradicted: 'contradicted',
  disputed: 'disputed',
  not_assessable: 'not_assessable',
};

export function evidenceStateOf(claim: Claim): EvidenceState {
  const status = claim.assessment?.corroboration_status;
  return status ? CORROBORATION_TO_STATE[status] : 'unassessed';
}

/** Chart / filter order: strongest outcome first, excluded last. */
export const EVIDENCE_STATE_ORDER: EvidenceState[] = [
  'verified',
  'partly_verified',
  'plausible',
  'unverified',
  'no_evidence',
  'disputed',
  'contradicted',
  'not_assessable',
  'unassessed',
];

/* ------------------------------------------------------------------- bands --- */

export const BAND_LABEL: Record<CredibilityBand, string> = {
  strong: 'Strong',
  moderate: 'Moderate',
  limited: 'Limited',
  weak: 'Weak',
  unsupported: 'Unsupported',
};

export const BAND_TONE: Record<CredibilityBand, Tone> = {
  strong: 'pos',
  moderate: 'info',
  limited: 'warn',
  weak: 'warn',
  unsupported: 'crit',
};

export const SEVERITY_TONE: Record<RiskSeverity, Tone> = {
  critical: 'crit',
  high: 'alert',
  medium: 'warn',
  low: 'pos',
  info: 'neutral',
};

export const SEVERITY_ORDER: RiskSeverity[] = ['critical', 'high', 'medium', 'low', 'info'];

export const RECOMMENDATION_LABEL: Record<ICRecommendation, string> = {
  advance: 'Advance',
  advance_with_conditions: 'Advance with conditions',
  further_diligence_required: 'Further diligence required',
  significant_concerns: 'Significant concerns',
  do_not_advance: 'Do not advance',
};

export const RECOMMENDATION_TONE: Record<ICRecommendation, Tone> = {
  advance: 'pos',
  advance_with_conditions: 'info',
  further_diligence_required: 'warn',
  significant_concerns: 'warn',
  do_not_advance: 'crit',
};

export function recommendationLabel(value: string | null | undefined): string {
  if (!value) return 'No recommendation';
  return RECOMMENDATION_LABEL[value as ICRecommendation] ?? humanise(value);
}

export function recommendationTone(value: string | null | undefined): Tone {
  if (!value) return 'neutral';
  return RECOMMENDATION_TONE[value as ICRecommendation] ?? 'neutral';
}

/* ------------------------------------------------------------- score effect --- */

export type ScoreEffect = 'lifts' | 'drags' | 'neutral' | 'excluded';

export const SCORE_EFFECT_META: Record<ScoreEffect, { label: string; tone: Tone }> = {
  lifts: { label: 'Lifts', tone: 'pos' },
  drags: { label: 'Drags', tone: 'crit' },
  neutral: { label: 'Neutral', tone: 'neutral' },
  excluded: { label: 'Excluded', tone: 'neutral' },
};

/* -------------------------------------------------------------- claim rows --- */

export type ImportanceTier = 'critical' | 'high' | 'medium' | 'low';

export const IMPORTANCE_TIER_LABEL: Record<ImportanceTier, string> = {
  critical: 'Thesis-critical',
  high: 'High',
  medium: 'Medium',
  low: 'Low',
};

export interface ClaimFlag {
  key: string;
  label: string;
  tone: Tone;
  title: string;
}

export interface ClaimRow {
  claim: Claim;
  id: string;
  statement: string;
  quote: string;
  page: number;
  typeKey: string;
  typeLabel: string;
  categoryKey: string;
  categoryLabel: string;
  state: EvidenceState;
  score: number | null;
  band: CredibilityBand | null;
  scorable: boolean;
  effect: ScoreEffect;
  /** Points above or below the no-information anchor; null when excluded. */
  effectDelta: number | null;
  confidence: number | null;
  importance: number;
  importanceTier: ImportanceTier;
  flags: ClaimFlag[];
  evidenceLinks: EvidenceLink[];
  supporting: number;
  contradicting: number;
  neutral: number;
  risks: Risk[];
  riskSeverity: RiskSeverity | null;
  questions: Question[];
  /** Pre-lowercased haystack so filtering never re-walks the object graph. */
  haystack: string;
}

function importanceTier(claim: Claim): ImportanceTier {
  if (claim.is_thesis_critical) return 'critical';
  if (claim.importance >= 0.7) return 'high';
  if (claim.importance >= 0.4) return 'medium';
  return 'low';
}

function claimFlags(claim: Claim): ClaimFlag[] {
  const flags: ClaimFlag[] = [];
  if (claim.is_thesis_critical) {
    flags.push({
      key: 'critical',
      label: 'Thesis-critical',
      tone: 'info',
      title: 'The investment case depends on this claim holding.',
    });
  }
  if (claim.needs_human_review) {
    flags.push({
      key: 'review',
      label: 'Needs review',
      tone: 'warn',
      title: claim.review_reasons.join('; ') || 'Flagged for human review.',
    });
  }
  if (claim.quote_verification === 'fuzzy') {
    flags.push({
      key: 'fuzzy',
      label: 'Approx. quote',
      tone: 'warn',
      title: `The source quote matched the document approximately (${claim.quote_match_score.toFixed(2)}).`,
    });
  }
  if (claim.quote_verification === 'not_found') {
    flags.push({
      key: 'noquote',
      label: 'Quote unmatched',
      tone: 'crit',
      title: 'The quote could not be located in the source document.',
    });
  }
  if (claim.from_visual) {
    flags.push({
      key: 'visual',
      label: 'From figure',
      tone: 'neutral',
      title: 'Read from a chart, table or image rather than body text.',
    });
  }
  if (claim.hedging_language) {
    flags.push({
      key: 'hedged',
      label: 'Hedged',
      tone: 'neutral',
      title: 'The deck states this with hedging language.',
    });
  }
  return flags;
}

/* ---------------------------------------------------------------- citations --- */

export interface CitationEntry {
  ref: string;
  kind: 'claim' | 'evidence' | 'other';
  claimId: string | null;
  title: string;
  quote: string | null;
  pageNumber: number | null;
  source: string | null;
  pmid: string | null;
  nctId: string | null;
  doi: string | null;
  url: string | null;
  abstract: string | null;
  journal: string | null;
  year: number | null;
}

function str(record: Record<string, unknown>, key: string): string | null {
  const value = record[key];
  return typeof value === 'string' && value.trim() ? value : null;
}

function numeric(record: Record<string, unknown>, key: string): number | null {
  const value = record[key];
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

function toCitation(raw: Record<string, unknown>): CitationEntry | null {
  const ref = str(raw, 'ref');
  if (!ref) return null;
  const rawKind = str(raw, 'kind');
  const kind: CitationEntry['kind'] =
    rawKind === 'claim' ? 'claim' : rawKind === 'evidence' ? 'evidence' : 'other';
  return {
    ref,
    kind,
    claimId: str(raw, 'claim_id'),
    title: str(raw, 'title') ?? str(raw, 'statement') ?? 'Untitled reference',
    quote: str(raw, 'quote') ?? str(raw, 'supporting_quote'),
    pageNumber: numeric(raw, 'page_number'),
    source: str(raw, 'source'),
    pmid: str(raw, 'pmid'),
    nctId: str(raw, 'nct_id'),
    doi: str(raw, 'doi'),
    url: str(raw, 'url'),
    abstract: str(raw, 'abstract'),
    journal: str(raw, 'journal'),
    year: numeric(raw, 'year') ?? numeric(raw, 'publication_year'),
  };
}

/* ------------------------------------------------------------ distributions --- */

export interface Slice {
  key: string;
  label: string;
  value: number;
  tone: Tone;
  hint?: string;
}

/* --------------------------------------------------------------- highlights --- */

/**
 * A one-line strength or risk for the executive summary, kept attached to
 * whatever produced it so the reader can follow it back to the evidence.
 */
export interface Highlight {
  id: string;
  /** The headline — the claim or finding itself. */
  text: string;
  /** Why it counts: the scorecard's stated reason, or the risk's description. */
  detail: string | null;
  /** Where the line came from — a scorecard dimension or a risk finding. */
  source: string;
  tone: Tone;
  claimId: string | null;
  severity: RiskSeverity | null;
}

function trimmed(value: string | undefined): string | null {
  return value && value.trim() ? value.trim() : null;
}

/* --------------------------------------------------------------- statistics --- */

export interface HeadlineStat {
  key: string;
  label: string;
  value: string;
  hint: string;
  tone?: Tone;
  /** Section id to jump to when the card is activated. */
  target?: string;
}

/* -------------------------------------------------------------- score facts --- */

export interface ScoreFacts {
  claimsScored: number | null;
  claimsExcluded: number | null;
  corroborated: number | null;
  unchecked: number | null;
  contradicted: number | null;
  informationRatio: number | null;
  anchor: number | null;
  verificationCoverage: number | null;
  pageCoverage: number | null;
  methodology: string | null;
  bandThresholds: Array<{ band: CredibilityBand; from: number }>;
}

function readScoreFacts(breakdown: Record<string, unknown>): ScoreFacts {
  const thresholdsRaw = breakdown.band_thresholds;
  const bandThresholds: ScoreFacts['bandThresholds'] = [];
  if (thresholdsRaw && typeof thresholdsRaw === 'object') {
    for (const [band, from] of Object.entries(thresholdsRaw as Record<string, unknown>)) {
      if (typeof from === 'number' && band in BAND_LABEL) {
        bandThresholds.push({ band: band as CredibilityBand, from });
      }
    }
    bandThresholds.sort((a, b) => b.from - a.from);
  }
  return {
    claimsScored: numeric(breakdown, 'claims_scored'),
    claimsExcluded: numeric(breakdown, 'claims_excluded_by_type'),
    corroborated: numeric(breakdown, 'corroborated_claims'),
    unchecked: numeric(breakdown, 'unchecked_claims'),
    contradicted: numeric(breakdown, 'contradicted_claims'),
    informationRatio: numeric(breakdown, 'information_ratio'),
    anchor: numeric(breakdown, 'no_information_anchor'),
    verificationCoverage: numeric(breakdown, 'verification_coverage'),
    pageCoverage: numeric(breakdown, 'page_coverage'),
    methodology: str(breakdown, 'methodology'),
    bandThresholds,
  };
}

/* ----------------------------------------------------------- run diagnostics --- */

export interface RetrievalFacts {
  recordsSeen: number | null;
  recordsRetained: number | null;
  claimsSearched: number | null;
  sources: Array<{ key: string; label: string; count: number }>;
}

const SOURCE_LABEL: Record<string, string> = {
  pubmed: 'PubMed',
  europe_pmc: 'Europe PMC',
  europepmc: 'Europe PMC',
  clinicaltrials_gov: 'ClinicalTrials.gov',
  clinicaltrials: 'ClinicalTrials.gov',
  openfda: 'openFDA',
  fda: 'openFDA',
};

export function sourceLabel(key: string): string {
  return SOURCE_LABEL[key] ?? humanise(key);
}

function readRetrieval(metrics: Record<string, unknown>, evidence: Evidence[]): RetrievalFacts {
  const stages = (metrics.stages ?? {}) as Record<string, unknown>;
  const retrieval = (stages.retrieval ?? {}) as Record<string, unknown>;
  const bySource = (retrieval.by_source ?? {}) as Record<string, unknown>;

  const sources = Object.entries(bySource)
    .filter(([, count]) => typeof count === 'number')
    .map(([key, count]) => ({ key, label: sourceLabel(key), count: count as number }))
    .sort((a, b) => b.count - a.count);

  if (sources.length === 0 && evidence.length > 0) {
    const tally = new Map<string, number>();
    for (const record of evidence) {
      tally.set(record.source, (tally.get(record.source) ?? 0) + 1);
    }
    for (const [key, count] of tally) sources.push({ key, label: sourceLabel(key), count });
    sources.sort((a, b) => b.count - a.count);
  }

  return {
    recordsSeen: numeric(retrieval, 'records_seen'),
    recordsRetained: numeric(retrieval, 'records_retained'),
    claimsSearched: numeric(retrieval, 'claims_searched'),
    sources,
  };
}

/* ------------------------------------------------------------------- model --- */

export interface RiskCard {
  risk: Risk;
  severity: RiskSeverity;
  headline: string;
  claim: ClaimRow | null;
  state: EvidenceState | null;
  questions: Question[];
  evidence: EvidenceLink[];
}

export interface ReportModel {
  runId: string;
  documentId: string;
  company: string;
  filename: string;
  subtitle: string | null;
  tags: string[];
  degraded: boolean;

  createdAt: string;
  durationMs: number | null;
  pipelineVersion: string;
  pageCount: number;

  title: string;
  executiveSummary: string;
  recommendationMarkdown: string;
  sections: ReportSection[];
  limitations: string[];

  score: number;
  band: CredibilityBand;
  confidence: number;
  recommendation: string | null;
  recommendationRationale: string;
  archetype: string | null;
  dimensions: DimensionScore[];
  scorecard: Scorecard | null;
  scoreFacts: ScoreFacts;
  scientificAssessment: Array<{ key: string; label: string; value: string }>;

  keyStrengths: Highlight[];
  keyRisks: Highlight[];
  confidenceNote: string;

  stats: HeadlineStat[];
  evidenceStateSlices: Slice[];
  claimTypeSlices: Slice[];
  stanceSlices: Slice[];
  sourceSlices: Slice[];
  retrieval: RetrievalFacts;

  claims: ClaimRow[];
  claimsById: Map<string, ClaimRow>;
  claimTypes: Array<{ key: string; label: string }>;
  risks: RiskCard[];
  questions: Question[];
  entities: Entity[];
  evidence: Evidence[];
  citations: CitationEntry[];
  citationsByRef: Map<string, CitationEntry>;
}

export interface ReportInput {
  run: RunDetail;
  report: Report;
  claims: Claim[];
  questions: Question[];
  risks: Risk[];
  entities: Entity[];
  evidence: Evidence[];
  evidenceByClaim: Record<string, EvidenceLink[]>;
}

const ASSESSMENT_LABELS: Array<[string, string]> = [
  ['biological_plausibility', 'Biological plausibility'],
  ['modality_precedent', 'Modality precedent'],
  ['first_in_class', 'First in class'],
  ['differentiation', 'Differentiation'],
  ['de_risking_achieved', 'De-risking achieved'],
  ['partnerability', 'Partnerability'],
  ['milestones_that_matter', 'Milestones that matter'],
  ['key_failure_mode', 'Key failure mode'],
];

export function buildReportModel(input: ReportInput): ReportModel {
  const { run, report, claims, questions, risks, entities, evidence, evidenceByClaim } = input;

  const scorecard = hasScorecard(report.scorecard) ? report.scorecard : null;
  const scoreFacts = readScoreFacts(report.score_breakdown ?? {});
  const anchor = scoreFacts.anchor ?? 58;

  const questionsByClaim = new Map<string, Question[]>();
  for (const question of questions) {
    for (const claimId of question.related_claim_ids) {
      const bucket = questionsByClaim.get(claimId);
      if (bucket) bucket.push(question);
      else questionsByClaim.set(claimId, [question]);
    }
  }

  const risksByClaim = new Map<string, Risk[]>();
  for (const risk of risks) {
    if (!risk.claim_id) continue;
    const bucket = risksByClaim.get(risk.claim_id);
    if (bucket) bucket.push(risk);
    else risksByClaim.set(risk.claim_id, [risk]);
  }

  const rows: ClaimRow[] = claims.map((claim) => {
    const assessment = claim.assessment;
    const state = evidenceStateOf(claim);
    const links = evidenceByClaim[claim.id] ?? [];
    const claimRisks = risksByClaim.get(claim.id) ?? [];
    const claimQuestions = questionsByClaim.get(claim.id) ?? [];

    const scorable = Boolean(assessment?.is_scorable);
    const score = assessment && scorable ? assessment.credibility_score : null;
    const delta = score === null ? null : Math.round((score - anchor) * 10) / 10;

    let effect: ScoreEffect = 'excluded';
    if (state === 'contradicted' || state === 'disputed') effect = 'drags';
    else if (!scorable || score === null) effect = 'excluded';
    else if (delta !== null && delta >= 3) effect = 'lifts';
    else if (delta !== null && delta <= -3) effect = 'drags';
    else effect = 'neutral';

    const severity =
      claimRisks.length > 0
        ? (SEVERITY_ORDER.find((level) => claimRisks.some((risk) => risk.severity === level)) ??
          null)
        : null;

    return {
      claim,
      id: claim.id,
      statement: claim.statement,
      quote: claim.verbatim_quote,
      page: claim.page_number,
      typeKey: claim.claim_type,
      typeLabel: humanise(claim.claim_type),
      categoryKey: claim.category,
      categoryLabel: humanise(claim.category),
      state,
      score,
      band: assessment && scorable ? assessment.credibility_band : null,
      scorable,
      effect,
      effectDelta: effect === 'excluded' ? null : delta,
      confidence: assessment ? assessment.confidence : null,
      importance: claim.importance,
      importanceTier: importanceTier(claim),
      flags: claimFlags(claim),
      evidenceLinks: links,
      supporting: assessment?.supporting_count ?? 0,
      contradicting: assessment?.contradicting_count ?? 0,
      neutral: assessment?.neutral_count ?? 0,
      risks: claimRisks,
      riskSeverity: severity,
      questions: claimQuestions,
      haystack: [
        claim.statement,
        claim.verbatim_quote,
        claim.claim_type,
        claim.category,
        assessment?.verdict ?? '',
        claim.entities.map((entity) => entity.canonical_name ?? entity.name).join(' '),
      ]
        .join(' ')
        .toLowerCase(),
    };
  });

  const claimsById = new Map(rows.map((row) => [row.id, row]));

  const riskCards: RiskCard[] = [...risks]
    .sort(
      (a, b) => SEVERITY_ORDER.indexOf(a.severity) - SEVERITY_ORDER.indexOf(b.severity),
    )
    .map((risk) => {
      const claim = risk.claim_id ? (claimsById.get(risk.claim_id) ?? null) : null;
      const evidenceIds = new Set(risk.evidence_ids);
      return {
        risk,
        severity: risk.severity,
        headline: risk.description,
        claim,
        state: claim ? claim.state : null,
        questions: risk.claim_id ? (questionsByClaim.get(risk.claim_id) ?? []) : [],
        evidence: (claim?.evidenceLinks ?? []).filter((link) =>
          evidenceIds.size === 0 ? false : evidenceIds.has(link.evidence.id),
        ),
      };
    });

  /* ------------------------------------------------------ distributions --- */

  const stateTally = new Map<EvidenceState, number>();
  for (const row of rows) stateTally.set(row.state, (stateTally.get(row.state) ?? 0) + 1);
  const evidenceStateSlices: Slice[] = EVIDENCE_STATE_ORDER.filter((state) =>
    stateTally.has(state),
  ).map((state) => ({
    key: state,
    label: EVIDENCE_STATE[state].label,
    value: stateTally.get(state) ?? 0,
    tone: EVIDENCE_STATE[state].tone,
    hint: EVIDENCE_STATE[state].description,
  }));

  const typeTally = new Map<string, number>();
  for (const row of rows) typeTally.set(row.categoryKey, (typeTally.get(row.categoryKey) ?? 0) + 1);
  const claimTypeSlices: Slice[] = [...typeTally.entries()]
    .sort((a, b) => b[1] - a[1])
    .map(([key, value]) => ({ key, label: humanise(key), value, tone: 'neutral' as Tone }));

  const stanceTally = new Map<string, number>();
  for (const links of Object.values(evidenceByClaim)) {
    for (const link of links) stanceTally.set(link.stance, (stanceTally.get(link.stance) ?? 0) + 1);
  }
  const STANCE_TONE: Record<string, Tone> = {
    supports: 'pos',
    contradicts: 'crit',
    mixed: 'warn',
    neutral: 'neutral',
    unrelated: 'neutral',
  };
  const stanceSlices: Slice[] = [...stanceTally.entries()]
    .sort((a, b) => b[1] - a[1])
    .map(([key, value]) => ({
      key,
      label: humanise(key),
      value,
      tone: STANCE_TONE[key] ?? 'neutral',
    }));

  const retrieval = readRetrieval(run.metrics ?? {}, evidence);
  const sourceSlices: Slice[] = retrieval.sources.map((source) => ({
    key: source.key,
    label: source.label,
    value: source.count,
    tone: 'neutral' as Tone,
  }));

  /* --------------------------------------------------------- statistics --- */

  const bucketTally = { verified: 0, unverified: 0, contradicted: 0, excluded: 0 };
  for (const row of rows) bucketTally[EVIDENCE_STATE[row.state].bucket] += 1;

  const notIndependentlyVerified = rows.filter(
    (row) => row.state === 'unverified' || row.state === 'no_evidence' || row.state === 'plausible',
  ).length;

  const criticalQuestions = questions.filter((q) => q.priority === 'critical').length;
  const criticalRisks = risks.filter(
    (r) => r.severity === 'critical' || r.severity === 'high',
  ).length;

  const stats: HeadlineStat[] = [
    {
      key: 'claims',
      label: 'Claims extracted',
      value: String(rows.length),
      hint: `across ${run.document.page_count} pages`,
      target: 'claims',
    },
    {
      key: 'verified',
      label: 'Verified',
      value: String(bucketTally.verified),
      hint: 'confirmed against an independent record',
      tone: bucketTally.verified > 0 ? 'pos' : 'neutral',
      target: 'evidence',
    },
    {
      key: 'unverified',
      label: 'Not independently verified',
      value: String(notIndependentlyVerified),
      hint: 'could not be checked — not a negative finding',
      target: 'evidence',
    },
    {
      key: 'contradicted',
      label: 'Contradicted',
      value: String(bucketTally.contradicted),
      hint: 'the outside record disagrees',
      tone: bucketTally.contradicted > 0 ? 'crit' : 'neutral',
      target: 'evidence',
    },
    {
      key: 'sources',
      label: 'Evidence records screened',
      value: String(retrieval.recordsSeen ?? evidence.length),
      hint:
        retrieval.sources.length > 0
          ? retrieval.sources.map((source) => source.label).join(' · ')
          : 'no literature search recorded',
      target: 'evidence',
    },
    {
      key: 'critical',
      label: 'Critical diligence items',
      value: String(criticalQuestions + criticalRisks),
      hint: `${criticalRisks} risks · ${criticalQuestions} questions`,
      tone: criticalQuestions + criticalRisks > 0 ? 'warn' : 'neutral',
      target: 'risks',
    },
  ];

  /* --------------------------------------------------------- highlights --- */

  const seenStrength = new Set<string>();
  const keyStrengths: Highlight[] = [];
  for (const dimension of scorecard?.dimensions ?? []) {
    if (!dimension.assessed) continue;
    for (const driver of dimension.positive_drivers) {
      // The driver's `statement` is the claim that lifted the dimension; its
      // `reason` is what the outside record said about it. Leading with the
      // reason alone reads as a caveat rather than a strength.
      const headline = trimmed(driver.statement) ?? trimmed(driver.reason);
      if (!headline) continue;
      const key = headline.toLowerCase();
      if (seenStrength.has(key)) continue;
      seenStrength.add(key);
      keyStrengths.push({
        id: `${dimension.dimension}:${keyStrengths.length}`,
        text: headline,
        detail: trimmed(driver.statement) ? trimmed(driver.reason) : null,
        source: dimension.label,
        tone: 'pos',
        claimId: trimmed(driver.claim_id),
        severity: null,
      });
    }
  }
  // Fall back to the corroborated thesis-critical claims when the scorecard
  // recorded no drivers — a real strength is still a strength.
  if (keyStrengths.length === 0) {
    for (const row of rows) {
      if (!row.claim.is_thesis_critical) continue;
      if (EVIDENCE_STATE[row.state].bucket !== 'verified') continue;
      keyStrengths.push({
        id: row.id,
        text: row.statement,
        detail: row.claim.assessment?.verdict ?? null,
        source: `Verified · deck p.${row.page}`,
        tone: 'pos',
        claimId: row.id,
        severity: null,
      });
    }
  }

  const keyRisks: Highlight[] = riskCards
    .filter((card) => card.severity === 'critical' || card.severity === 'high')
    .map((card) => ({
      id: card.risk.id,
      text: card.risk.title,
      detail: card.risk.description,
      source: humanise(card.risk.category),
      tone: SEVERITY_TONE[card.severity],
      claimId: card.risk.claim_id,
      severity: card.severity,
    }));

  const confidenceParts: string[] = [];
  if (scoreFacts.claimsScored !== null) {
    confidenceParts.push(
      `${scoreFacts.claimsScored} of ${rows.length} claims were scorable; the rest are forward-looking, financial or strategic statements the method deliberately excludes.`,
    );
  }
  if (scoreFacts.unchecked !== null && scoreFacts.unchecked > 0) {
    confidenceParts.push(
      `${scoreFacts.unchecked} scorable claims could not be checked against an independent record, which caps confidence rather than reducing the score.`,
    );
  }
  if (scoreFacts.verificationCoverage !== null) {
    confidenceParts.push(
      `Registry and regulatory verification reached ${Math.round(scoreFacts.verificationCoverage * 100)}% of the claims that admit it.`,
    );
  }
  const confidenceNote = confidenceParts.join(' ');

  /* ---------------------------------------------------------- citations --- */

  const citations = report.citations
    .map((raw) => toCitation(raw as Record<string, unknown>))
    .filter((entry): entry is CitationEntry => entry !== null);
  const citationsByRef = new Map(citations.map((entry) => [entry.ref, entry]));

  /* ------------------------------------------------------------ profile --- */

  const profile = run.profile;
  const tags = [
    profile?.lead_indication,
    profile?.modality,
    profile?.development_stage ? humanise(profile.development_stage) : null,
    profile?.company_stage ? humanise(profile.company_stage) : null,
  ].filter((value): value is string => Boolean(value));

  const scientificAssessment = ASSESSMENT_LABELS.map(([key, label]) => {
    const value = report.scientific_assessment?.[key];
    return typeof value === 'string' && value.trim() ? { key, label, value } : null;
  }).filter((entry): entry is { key: string; label: string; value: string } => entry !== null);

  return {
    runId: run.id,
    documentId: run.document_id,
    company: profile?.company_name ?? run.document.filename.replace(/\.pdf$/i, ''),
    filename: run.document.filename,
    subtitle: profile?.one_liner ?? null,
    tags,
    degraded: run.degraded,

    createdAt: report.created_at,
    durationMs: run.duration_ms,
    pipelineVersion: run.pipeline_version,
    pageCount: run.document.page_count,

    title: report.title,
    executiveSummary: report.executive_summary,
    recommendationMarkdown: report.recommendation,
    sections: [...report.sections].sort((a, b) => a.order - b.order),
    limitations: report.limitations,

    score: report.overall_score,
    band: report.overall_band,
    confidence: report.confidence,
    recommendation: report.ic_recommendation ?? scorecard?.recommendation ?? null,
    recommendationRationale: scorecard?.recommendation_rationale ?? '',
    archetype: scorecard?.archetype ?? null,
    dimensions: scorecard?.dimensions ?? [],
    scorecard,
    scoreFacts,
    scientificAssessment,

    keyStrengths: keyStrengths.slice(0, 6),
    keyRisks: keyRisks.slice(0, 6),
    confidenceNote,

    stats,
    evidenceStateSlices,
    claimTypeSlices,
    stanceSlices,
    sourceSlices,
    retrieval,

    claims: rows,
    claimsById,
    claimTypes: [...new Set(rows.map((row) => row.typeKey))]
      .sort()
      .map((key) => ({ key, label: humanise(key) })),
    risks: riskCards,
    questions: [...questions].sort((a, b) => a.rank - b.rank),
    entities,
    evidence,
    citations,
    citationsByRef,
  };
}
