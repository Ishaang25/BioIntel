/**
 * The report's top-level structure.
 *
 * The memo used to be one continuous scroll of nine sections: verdict, summary,
 * scorecard, nine sub-sections of analysis, every finding, every claim, the
 * evidence distribution, the questions, and the appendix. All of it is worth
 * having and none of it is worth showing at once — the first screen has one
 * job, which is to answer *what is the score, should we proceed, why, and what
 * are the biggest risks*. Everything else is a place a reader goes on purpose.
 *
 * So the report is a small set of views. The summary is the answer; the rest
 * are the working. Nothing was removed — the deep-dive sections are the same
 * components, reached by a click instead of by scrolling past them.
 *
 * This module is the single source of truth for that map, kept pure so the
 * cross-view navigation (a chart segment filtering the claim table, a citation
 * opening a claim, a risk tracing to its source) can be tested without a DOM.
 */

export type ViewId =
  | 'summary'
  | 'score'
  | 'analysis'
  | 'risks'
  | 'claims'
  | 'evidence'
  | 'questions'
  | 'appendix';

export type ViewGroup = 'answer' | 'working' | 'reference';

export interface ViewDef {
  id: ViewId;
  label: string;
  /** One line, shown on the navigation cards. Says what the reader will find. */
  blurb: string;
  group: ViewGroup;
  /** Which `model` field supplies the count beside the label, if any. */
  countKey?: 'sections' | 'risks' | 'claims' | 'evidence' | 'questions';
}

/**
 * Order is the reading order the product recommends: the answer, then the
 * evidence for it, then the reference material.
 */
export const VIEWS: readonly ViewDef[] = [
  {
    id: 'summary',
    label: 'Summary',
    blurb: 'The verdict, why it was reached, and the risks that drive it.',
    group: 'answer',
  },
  {
    id: 'risks',
    label: 'Risks',
    blurb: 'Every finding the analysis raised, ordered by severity.',
    group: 'working',
    countKey: 'risks',
  },
  {
    id: 'evidence',
    label: 'Evidence',
    blurb: 'How the claims resolved against the outside record, and what was searched.',
    group: 'working',
    countKey: 'evidence',
  },
  {
    id: 'claims',
    label: 'Claims',
    blurb: 'Every extracted claim with its verbatim source and adjudication.',
    group: 'working',
    countKey: 'claims',
  },
  {
    id: 'questions',
    label: 'Questions',
    blurb: 'What to put to management, ordered by what would change the decision.',
    group: 'working',
    countKey: 'questions',
  },
  {
    id: 'score',
    label: 'Scorecard',
    blurb: 'How the score was computed, dimension by dimension.',
    group: 'reference',
  },
  {
    id: 'analysis',
    label: 'Full memo',
    blurb: 'The complete written assessment, with citations to every claim.',
    group: 'reference',
    countKey: 'sections',
  },
  {
    id: 'appendix',
    label: 'Appendix',
    blurb: 'References, entities, limitations and the provenance of this run.',
    group: 'reference',
  },
] as const;

const VIEW_IDS = new Set<string>(VIEWS.map((view) => view.id));

/**
 * Which view owns each anchorable section.
 *
 * Sections keep the ids they always had, so every existing deep link, citation
 * target and `goToSection` call keeps working — it now switches view first.
 */
const SECTION_VIEW: Record<string, ViewId> = {
  // The summary view holds three stacked sections.
  verdict: 'summary',
  overview: 'summary',
  summary: 'summary',
  explore: 'summary',
  // One section per deep-dive view.
  score: 'score',
  analysis: 'analysis',
  risks: 'risks',
  claims: 'claims',
  evidence: 'evidence',
  questions: 'questions',
  appendix: 'appendix',
};

/** The view a section lives in; the summary for anything unrecognised. */
export function viewForSection(sectionId: string): ViewId {
  return SECTION_VIEW[sectionId] ?? 'summary';
}

/** Parses a `#fragment` into a view, ignoring anything unknown. */
export function viewFromHash(hash: string): ViewId | null {
  const fragment = hash.replace(/^#/, '').trim();
  if (!fragment) return null;
  if (VIEW_IDS.has(fragment)) return fragment as ViewId;
  const mapped = SECTION_VIEW[fragment];
  return mapped ?? null;
}

export function viewDef(id: ViewId): ViewDef {
  return VIEWS.find((view) => view.id === id) ?? VIEWS[0]!;
}
