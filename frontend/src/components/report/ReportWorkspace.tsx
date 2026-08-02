'use client';

import Link from 'next/link';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import {
  BAND_LABEL,
  buildReportModel,
  recommendationLabel,
  recommendationTone,
} from '@/lib/report-model';
import type { ReportInput, ReportModel } from '@/lib/report-model';
import { Badge, Callout, cx, toneClasses } from '@/components/ui/primitives';
import { ClaimDrawer } from './ClaimDrawer';
import { CitationPopover, type CitationAnchor } from './CitationPopover';
import { ExportMenu } from './ExportMenu';
import { ReportProvider, useReport, type ClaimFilter, type ReportContextValue } from './context';
import { ReportNav, type NavCounts } from './ReportNav';
import { viewDef, viewForSection, viewFromHash, type ViewId } from './navigation';
import { AnalysisSection } from './sections/Analysis';
import { AppendixSection } from './sections/Appendix';
import { ClaimsSection, type ClaimFilterRequest } from './sections/Claims';
import { EvidenceSection } from './sections/Evidence';
import { ExecutiveSummarySection } from './sections/ExecutiveSummary';
import { AtAGlanceSection, ExploreSection } from './sections/Explore';
import { QuestionsSection } from './sections/Questions';
import { RisksSection } from './sections/Risks';
import { ScoreBreakdownSection } from './sections/ScoreBreakdown';
import { VerdictSection } from './sections/Verdict';

const HOVER_CLOSE_DELAY = 140;

/**
 * The diligence workspace.
 *
 * Derivation happens once here, in a memo over the raw API payloads, and the
 * result is handed to every section through context. Cross-section navigation
 * — a chart segment filtering the claim table, a citation opening a claim, a
 * risk tracing back to its source — is coordinated at this level so no section
 * needs to know about any other.
 *
 * The report is split into views rather than stacked into one scroll (see
 * `./navigation`). That changes one thing for the sections: `goToSection` may
 * now have to switch view before it can scroll, so the scroll waits a frame for
 * the target to exist. Everything else about them is unchanged.
 */
export function ReportWorkspace(props: ReportInput) {
  const model = useMemo(() => buildReportModel(props), [props]);

  const [view, setView] = useState<ViewId>('summary');
  const [activeClaimId, setActiveClaimId] = useState<string | null>(null);
  const [citation, setCitation] = useState<CitationAnchor | null>(null);
  const [claimFilter, setClaimFilter] = useState<ClaimFilterRequest>({ token: 0 });
  const closeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const cancelClose = () => {
    if (closeTimer.current) {
      clearTimeout(closeTimer.current);
      closeTimer.current = null;
    }
  };

  /**
   * The current view, readable synchronously.
   *
   * `selectView` needs to know whether the view is actually changing in order
   * to decide whether to reset the scroll position, and that decision cannot
   * live inside the state updater: React may run an updater more than once,
   * and scrolling the window is not something to do twice.
   */
  const viewRef = useRef<ViewId>('summary');

  const selectView = useCallback((next: ViewId, { resetScroll = true } = {}) => {
    const changed = viewRef.current !== next;
    viewRef.current = next;
    setView(next);

    // A view is a place, so it belongs in the URL: a link to the claim
    // explorer reopens the claim explorer. `replaceState` rather than a push,
    // because tabbing through views is browsing one document, not visiting
    // eight pages — Back should leave the report, not walk the tab history.
    if (window.location.hash !== `#${next}`) {
      window.history.replaceState(null, '', `#${next}`);
    }
    // Landing halfway down a view the reader has not seen before is
    // disorienting, so a newly opened view starts at its top — unless the
    // caller is about to scroll to a specific section inside it.
    if (changed && resetScroll) window.scrollTo({ top: 0, behavior: 'auto' });
  }, []);

  // Honour the fragment on arrival, and follow it if the reader edits it or
  // uses a link that only changes the hash.
  useEffect(() => {
    const apply = () => {
      const next = viewFromHash(window.location.hash);
      if (next && next !== viewRef.current) {
        viewRef.current = next;
        setView(next);
      }
    };
    apply();
    window.addEventListener('hashchange', apply);
    return () => window.removeEventListener('hashchange', apply);
  }, []);

  /**
   * Reveals a section wherever it now lives.
   *
   * Sections keep the ids they always had, so every existing caller works
   * unchanged; the only addition is switching to the owning view first and
   * waiting a frame for the element to mount before scrolling to it.
   */
  const goToSection = useCallback(
    (sectionId: string) => {
      selectView(viewForSection(sectionId), { resetScroll: false });
      requestAnimationFrame(() => {
        document.getElementById(sectionId)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
      });
    },
    [selectView],
  );

  const openClaim = useCallback(
    (claimId: string) => {
      if (!model.claimsById.has(claimId)) return;
      setActiveClaimId(claimId);
    },
    [model.claimsById],
  );

  const openCitation = useCallback((ref: string, anchor: HTMLElement, pinned: boolean) => {
    cancelClose();
    setCitation((current) => {
      // A hover must never demote a popover the reader deliberately pinned.
      if (current && current.ref === ref && current.pinned && !pinned) return current;
      return { ref, element: anchor, pinned };
    });
  }, []);

  const closeCitation = useCallback((immediate = false) => {
    cancelClose();
    if (immediate) {
      setCitation(null);
      return;
    }
    closeTimer.current = setTimeout(() => {
      setCitation((current) => (current?.pinned ? current : null));
    }, HOVER_CLOSE_DELAY);
  }, []);

  const filterClaims = useCallback(
    (filter: ClaimFilter) => {
      setClaimFilter((current) => ({ token: current.token + 1, ...filter }));
      goToSection('claims');
    },
    [goToSection],
  );

  const context = useMemo<ReportContextValue>(
    () => ({
      model,
      openClaim,
      openCitation,
      closeCitation,
      goToSection,
      filterClaims,
      lookupClaim: (claimId: string) => model.claimsById.get(claimId),
    }),
    [model, openClaim, openCitation, closeCitation, goToSection, filterClaims],
  );

  const counts = useMemo<NavCounts>(
    () => ({
      sections: model.sections.length,
      risks: model.risks.length,
      claims: model.claims.length,
      evidence: model.evidence.length,
      questions: model.questions.length,
    }),
    [model],
  );

  const activeClaim = activeClaimId ? (model.claimsById.get(activeClaimId) ?? null) : null;
  const citationEntry = citation ? model.citationsByRef.get(citation.ref) : undefined;
  const citationClaim = citationEntry?.claimId
    ? model.claimsById.get(citationEntry.claimId)
    : undefined;

  return (
    <ReportProvider value={context}>
      <ReportTitleBar model={model} />
      <ReportNav active={view} counts={counts} onSelect={(next) => selectView(next)} />

      <div
        role="tabpanel"
        id={`report-panel-${view}`}
        aria-labelledby={`report-tab-${view}`}
        tabIndex={-1}
        className="mx-auto w-full max-w-[1600px] px-4 pb-24 pt-6 sm:px-6"
      >
        <div className="space-y-8">
          {model.unavailable.length > 0 && (
            <Callout tone="warn" title="Part of this report could not be loaded">
              The {model.unavailable.join(', ')}{' '}
              {model.unavailable.length === 1 ? 'endpoint' : 'endpoints'} did not respond, so
              {model.unavailable.length === 1 ? ' that section is' : ' those sections are'} empty
              below. This is a loading failure, not a finding — the analysis itself completed.
              Reload to try again.
            </Callout>
          )}

          {model.degraded && (
            <Callout tone="warn" title="Degraded analysis">
              This run was produced without a language-model provider. Claims and evidence were
              matched by deterministic lexical rules; figures were not interpreted and evidence was
              not semantically adjudicated. Re-run with a provider configured before relying on
              this.
            </Callout>
          )}

          {view === 'summary' && (
            <>
              <VerdictSection />
              <ExecutiveSummarySection />
              <ExploreSection />
              <AtAGlanceSection />
            </>
          )}

          {view !== 'summary' && <ViewIntro view={view} />}

          {view === 'score' && <ScoreBreakdownSection />}
          {view === 'analysis' && <AnalysisSection />}
          {view === 'risks' && <RisksSection />}
          {/* Mounted only while selected: the claim table is virtualised over
              every claim in the deck, and the appendix renders one tab at a
              time. Neither should cost anything on the summary. */}
          {view === 'claims' && <ClaimsSection request={claimFilter} />}
          {view === 'evidence' && <EvidenceSection />}
          {view === 'questions' && <QuestionsSection />}
          {view === 'appendix' && <AppendixSection />}
        </div>
      </div>

      <ClaimDrawer
        row={activeClaim}
        open={activeClaim !== null}
        onClose={() => setActiveClaimId(null)}
      />

      {citation && (
        <div onMouseEnter={cancelClose} onMouseLeave={() => closeCitation()}>
          <CitationPopover
            anchor={citation}
            entry={citationEntry}
            claim={citationClaim}
            onClose={() => closeCitation(true)}
            onOpenClaim={openClaim}
          />
        </div>
      )}
    </ReportProvider>
  );
}

/**
 * A one-line orientation at the top of every deep-dive view, plus the way back.
 *
 * Arriving in the claim explorer from a chart segment is otherwise a jump with
 * no context: the reader needs to know where they are and how to get back to
 * the answer without hunting for it.
 */
function ViewIntro({ view }: { view: ViewId }) {
  const { goToSection } = useReport();
  const def = viewDef(view);
  return (
    <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 border-b border-line pb-3">
      <p className="text-[13px] text-fg-2">{def.blurb}</p>
      <button
        type="button"
        onClick={() => goToSection('verdict')}
        className="text-[13px] text-fg-3 underline decoration-line-strong underline-offset-2 transition-colors hover:text-fg"
      >
        ← Back to the summary
      </button>
    </div>
  );
}

/**
 * The persistent context bar: which company, what the answer was, and how to
 * take it away. Stays visible while the reader moves through the report.
 */
function ReportTitleBar({ model }: { model: ReportModel }) {
  const tone = recommendationTone(model.recommendation);

  return (
    <div className="sticky top-0 z-30 border-b border-line bg-canvas/85 backdrop-blur">
      <div className="mx-auto flex min-h-14 w-full max-w-[1600px] flex-wrap items-center gap-x-4 gap-y-2 px-4 py-2 sm:px-6">
        <div className="flex min-w-0 flex-1 items-center gap-3">
          <Link
            href={`/documents/${model.documentId}`}
            className="shrink-0 rounded text-fg-3 transition-colors hover:text-fg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-fg/30"
            aria-label="Back to the document"
          >
            <svg width="15" height="15" viewBox="0 0 16 16" aria-hidden>
              <path
                d="M10 3.5L5.5 8l4.5 4.5"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.5"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
          </Link>

          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <h1 className="truncate text-[15px] font-semibold tracking-[-0.01em] text-fg">
                {model.company}
              </h1>
              <span
                className={cx(
                  'num hidden shrink-0 text-[13px] font-semibold sm:inline',
                  toneClasses.text[tone],
                )}
              >
                {model.score.toFixed(0)}
              </span>
              <span className="hidden shrink-0 text-2xs text-fg-3 sm:inline">
                {BAND_LABEL[model.band]}
              </span>
            </div>
            <div className="mt-0.5 hidden flex-wrap items-center gap-1.5 lg:flex">
              {model.tags.slice(0, 4).map((tag) => (
                <span key={tag} className="text-2xs text-fg-3">
                  {tag}
                </span>
              ))}
            </div>
          </div>
        </div>

        <Badge tone={tone} className="hidden md:inline-flex">
          {recommendationLabel(model.recommendation)}
        </Badge>

        <ExportMenu model={model} />
      </div>
    </div>
  );
}
