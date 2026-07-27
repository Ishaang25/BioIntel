/**
 * API types.
 *
 * Hand-mirrored from the FastAPI response models rather than generated, so the
 * frontend depends on a stable, reviewed contract. `npm run typecheck` fails
 * loudly if a component drifts from what the API actually returns.
 */

export type RunStatus = 'pending' | 'running' | 'succeeded' | 'failed' | 'cancelled';
export type StageStatus = 'pending' | 'running' | 'succeeded' | 'failed' | 'skipped';
export type CredibilityBand = 'strong' | 'moderate' | 'limited' | 'weak' | 'unsupported';
export type Stance = 'supports' | 'contradicts' | 'mixed' | 'neutral' | 'unrelated';
export type RiskSeverity = 'critical' | 'high' | 'medium' | 'low' | 'info';
export type QuestionPriority = 'critical' | 'high' | 'medium' | 'low';
export type QuoteVerification = 'exact' | 'fuzzy' | 'not_found' | 'not_applicable';

export interface Paginated<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export interface ApiError {
  code: string;
  message: string;
  detail?: Record<string, unknown>;
}

export interface DocumentSummary {
  id: string;
  filename: string;
  content_hash: string;
  size_bytes: number;
  page_count: number;
  is_parsed: boolean;
  requires_ocr: boolean;
  notes: string | null;
  pdf_metadata: Record<string, unknown>;
  created_at: string;
}

export interface DocumentDetail extends DocumentSummary {
  latest_run_id: string | null;
  run_count: number;
}

export interface Stage {
  stage: string;
  sequence: number;
  status: StageStatus;
  started_at: string | null;
  finished_at: string | null;
  duration_ms: number | null;
  error_message: string | null;
  metrics: Record<string, unknown>;
}

export interface CompanyProfile {
  company_name: string | null;
  one_liner: string | null;
  founded_year: number | null;
  headquarters: string | null;
  company_stage: string | null;
  lead_program: string | null;
  lead_indication: string | null;
  modality: string | null;
  development_stage: string | null;
  pipeline: Array<Record<string, unknown>>;
  team: Array<Record<string, unknown>>;
  funding: Record<string, unknown>;
  partnerships: string[];
  ip_position: string | null;
  business_model: string | null;
  source_pages: number[];
}

export interface Run {
  id: string;
  document_id: string;
  status: RunStatus;
  current_stage: string | null;
  progress: number;
  started_at: string | null;
  finished_at: string | null;
  duration_ms: number | null;
  error_code: string | null;
  error_message: string | null;
  pipeline_version: string;
  created_at: string;
}

export interface RunDetail extends Run {
  document: DocumentSummary;
  stages: Stage[];
  counts: Record<string, number>;
  metrics: Record<string, unknown>;
  config: Record<string, unknown>;
  profile: CompanyProfile | null;
  degraded: boolean;
}

export interface Assessment {
  credibility_score: number;
  credibility_band: CredibilityBand;
  confidence: number;
  supporting_count: number;
  contradicting_count: number;
  neutral_count: number;
  evidence_quality: number;
  consistency: number;
  novelty: number;
  verdict: string;
  key_uncertainties: string[];
  score_breakdown: Record<string, unknown>;
}

export interface EntityRef {
  id: string;
  entity_type: string;
  name: string;
  canonical_name: string | null;
}

export interface Claim {
  id: string;
  statement: string;
  verbatim_quote: string;
  page_number: number;
  from_visual: boolean;
  category: string;
  claimed_evidence_tier: string;
  quantitative: Array<Record<string, unknown>>;
  is_scientific: boolean;
  is_falsifiable: boolean;
  hedging_language: boolean;
  importance: number;
  is_thesis_critical: boolean;
  extraction_confidence: number;
  quote_verification: QuoteVerification;
  quote_match_score: number;
  needs_human_review: boolean;
  review_reasons: string[];
  entities: EntityRef[];
  assessment: Assessment | null;
}

export interface Evidence {
  id: string;
  source: string;
  external_id: string;
  title: string;
  abstract: string | null;
  journal: string | null;
  publication_year: number | null;
  authors: string[];
  study_design: string;
  doi: string | null;
  pmid: string | null;
  nct_id: string | null;
  url: string | null;
  citation_count: number | null;
  is_retracted: boolean;
  is_preprint: boolean;
  trial: Record<string, unknown>;
}

export interface EvidenceLink {
  id: string;
  claim_id: string;
  stance: Stance;
  strength: number;
  relevance: number;
  similarity: number;
  rationale: string;
  supporting_quote: string;
  quote_verification: QuoteVerification;
  caveats: string[];
  evidence: Evidence;
}

export interface ClaimDetail extends Claim {
  evidence_links: EvidenceLink[];
}

export interface Entity {
  id: string;
  entity_type: string;
  name: string;
  canonical_name: string | null;
  aliases: string[];
  description: string | null;
  role_in_program: string | null;
  salience: number;
  mention_count: number;
  source_pages: number[];
  extraction_confidence: number;
}

export interface Risk {
  id: string;
  claim_id: string | null;
  category: string;
  severity: RiskSeverity;
  title: string;
  description: string;
  basis: string;
  evidence_ids: string[];
  source_pages: number[];
  is_rule_based: boolean;
}

export interface Question {
  id: string;
  question: string;
  rationale: string;
  priority: QuestionPriority;
  category: string;
  what_good_looks_like: string;
  related_claim_ids: string[];
  rank: number;
}

export interface ReportSection {
  id: string;
  heading: string;
  body_markdown: string;
  citations: string[];
  basis: string;
  order: number;
}

export interface Report {
  id: string;
  run_id: string;
  title: string;
  executive_summary: string;
  sections: ReportSection[];
  overall_score: number;
  overall_band: CredibilityBand;
  confidence: number;
  recommendation: string;
  score_breakdown: Record<string, unknown>;
  citations: Array<Record<string, unknown>>;
  limitations: string[];
  markdown: string;
  created_at: string;
}

export interface Health {
  status: string;
  version: string;
  environment: string;
  database: string;
  llm_provider: string;
  llm_degraded: boolean;
  retrieval_enabled: boolean;
  job_mode: string;
  queue: Record<string, number>;
}

export interface UploadResponse {
  document: DocumentSummary;
  created: boolean;
  run: Run | null;
}

export interface ProgressEvent {
  run_id: string;
  status: RunStatus;
  current_stage: string | null;
  progress: number;
  error_code: string | null;
  error_message: string | null;
  stages: Array<{ stage: string; status: StageStatus; duration_ms: number | null; error: string | null }>;
  counts: Record<string, number>;
}
