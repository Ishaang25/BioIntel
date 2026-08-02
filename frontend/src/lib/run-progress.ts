/**
 * Making a multi-minute analysis legible while it runs.
 *
 * Two problems, one shape. A stage name alone does not tell a reader what is
 * happening ("Adjudication" means nothing to an investor) or why the wait is
 * reasonable. And a bar that sits at 95% for two minutes reads as hung even
 * when the backend is working normally — the report stage is a single unit of
 * work that generates ~15k tokens.
 *
 * So: every stage carries a plain description of what BioIntel is doing and
 * why it takes time, the backend sends the live sub-step where it has one
 * (`current_activity`), and the remaining time is estimated from the run's own
 * observed rate rather than guessed at.
 */

/** What each pipeline stage is, in the reader's terms. */
export interface StageNarrative {
  /** Sentence case, no jargon. Replaces the humanised enum name. */
  label: string;
  /** Present tense, shown while the stage is running. */
  doing: string;
  /** Why this step exists, shown when the stage is expanded. */
  why: string;
}

/** Mirrors `app.core.enums.STAGE_ORDER`. */
export const STAGE_ORDER = [
  'parse',
  'page_understanding',
  'profile',
  'entities',
  'claims',
  'retrieval',
  'adjudication',
  'assessment',
  'questions',
  'report',
] as const;

export const STAGE_NARRATIVE: Record<string, StageNarrative> = {
  parse: {
    label: 'Reading the PDF',
    doing: 'Extracting text, tables and page images from the deck',
    why: 'Every later step cites a page of this document, so the text has to be recovered first.',
  },
  page_understanding: {
    label: 'Interpreting the slides',
    doing: 'Reading each page visually, including charts and scanned images',
    why: 'Decks put their real data in figures. Pages are read visually so a chart is not lost.',
  },
  profile: {
    label: 'Identifying the company',
    doing: 'Working out the company, its stage, modality and lead programmes',
    why: 'A platform company and a single-asset company are not judged on the same axes.',
  },
  entities: {
    label: 'Extracting entities',
    doing: 'Cataloguing targets, diseases, drugs, biomarkers and endpoints',
    why: 'These become the search terms used to find independent evidence.',
  },
  claims: {
    label: 'Extracting claims',
    doing: 'Pulling out every scientific assertion with its verbatim source',
    why: 'Each claim is anchored to a page so any conclusion can be traced back to the deck.',
  },
  retrieval: {
    label: 'Searching the literature',
    doing: 'Querying PubMed, Europe PMC, ClinicalTrials.gov and openFDA',
    why: 'This is the slowest step: it depends on public APIs answering, one query per claim.',
  },
  adjudication: {
    label: 'Weighing the evidence',
    doing: 'Judging each claim against what the outside record actually says',
    why: 'Deciding whether evidence supports, contradicts or simply does not address a claim.',
  },
  assessment: {
    label: 'Scoring',
    doing: 'Computing the scorecard across ten dimensions',
    why: 'Scoring is deterministic — the same claims and evidence always give the same number.',
  },
  questions: {
    label: 'Drafting questions',
    doing: 'Writing the diligence questions to put to management',
    why: 'The gaps found while adjudicating become the questions worth asking.',
  },
  report: {
    label: 'Writing the memo',
    doing: 'Generating the executive summary, assessment and final report',
    why: 'The longest single model call in the run. It writes the memo in one pass so the argument holds together.',
  },
};

export function stageLabel(stage: string): string {
  return STAGE_NARRATIVE[stage]?.label ?? humaniseStage(stage);
}

function humaniseStage(stage: string): string {
  const words = stage.replace(/_/g, ' ');
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/**
 * The single line answering "what is happening right now".
 *
 * Prefers the backend's live sub-step, which is specific ("Reading page 8 of
 * 25"), and falls back to the stage's own description when a stage reports as
 * one unit.
 */
export function currentActivityLine(
  stage: string | null,
  activity: string | null | undefined,
): string | null {
  if (activity) return activity;
  if (!stage) return null;
  return STAGE_NARRATIVE[stage]?.doing ?? null;
}

/**
 * Remaining milliseconds, extrapolated from the run's own rate so far.
 *
 * Returns `null` rather than a number whenever the estimate would be
 * dishonest: too early to have a rate, already finished, or so far out that
 * quoting it would be a guess dressed as a measurement. An absent estimate is
 * a better experience than a wrong one — a countdown that keeps growing is
 * worse than no countdown.
 */
export function estimateRemainingMs(progress: number, elapsedMs: number): number | null {
  if (!Number.isFinite(progress) || !Number.isFinite(elapsedMs)) return null;
  if (progress <= 0.04 || progress >= 1) return null;
  // Below this the sample is too short for the rate to mean anything.
  if (elapsedMs < 20_000) return null;

  const remaining = (elapsedMs / progress) * (1 - progress);
  if (remaining > 30 * 60_000) return null;
  return Math.round(remaining);
}

/** Coarse, honest phrasing. Nobody needs "1 min 47 s remaining". */
export function formatRemaining(remainingMs: number): string {
  const minutes = remainingMs / 60_000;
  if (minutes < 1) return 'under a minute remaining';
  if (minutes < 1.5) return 'about a minute remaining';
  return `about ${Math.round(minutes)} minutes remaining`;
}
