'use client';

import Link from 'next/link';
import { useCallback, useMemo, useRef, useState } from 'react';

import { BAND_LABEL, buildReportModel, recommendationLabel, recommendationTone } from '@/lib/report-model';
import type { ReportInput, ReportModel } from '@/lib/report-model';
import { Badge, Callout, cx, toneClasses } from '@/components/ui/primitives';
import { ClaimDrawer } from './ClaimDrawer';
import { CitationPopover, type CitationAnchor } from './CitationPopover';
import { ExportMenu } from './ExportMenu';
import { ReportProvider, type ClaimFilter, type ReportContextValue } from './context';
import { ReportRail, ReportStrip, useScrollSpy, type NavSection } from './ReportSidebar';
import { AnalysisSection } from './sections/Analysis';
import { AppendixSection } from './sections/Appendix';
import { ClaimsSection, type ClaimFilterRequest } from './sections/Claims';
import { EvidenceSection } from './sections/Evidence';
import { ExecutiveSummarySection } from './sections/ExecutiveSummary';
import { OverviewSection } from './sections/Overview';
import { QuestionsSection } from './sections/Questions';
import { RisksSection } from './sections/Risks';
import { ScoreBreakdownSection } from './sections/ScoreBreakdown';

const HOVER_CLOSE_DELAY = 140;

/**
 * The diligence workspace.
 *
 * Derivation happens once here, in a memo over the raw API payloads, and the
 * result is handed to every section through context. Cross-section navigation
 * — a chart segment filtering the claim table, a citation opening a claim, a
 * risk tracing back to its source — is coordinated at this level so no section
 * needs to know about any other.
 */
export function ReportWorkspace(props: ReportInput) {
  const model = useMemo(() => buildReportModel(props), [props]);

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

  const goToSection = useCallback((sectionId: string) => {
    document.getElementById(sectionId)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }, []);

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
      requestAnimationFrame(() => goToSection('claims'));
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

  const sections: NavSection[] = useMemo(
    () => [
      { id: 'overview', label: 'Overview' },
      { id: 'summary', label: 'Executive summary' },
      { id: 'score', label: 'Score breakdown' },
      { id: 'analysis', label: 'Analysis', count: model.sections.length },
      { id: 'risks', label: 'Risks', count: model.risks.length },
      { id: 'claims', label: 'Claims', count: model.claims.length },
      { id: 'evidence', label: 'Evidence', count: model.evidence.length },
      { id: 'questions', label: 'Management questions', count: model.questions.length },
      { id: 'appendix', label: 'Appendix' },
    ],
    [model],
  );

  const sectionIds = useMemo(() => sections.map((section) => section.id), [sections]);
  const activeSection = useScrollSpy(sectionIds);

  const activeClaim = activeClaimId ? (model.claimsById.get(activeClaimId) ?? null) : null;
  const citationEntry = citation ? model.citationsByRef.get(citation.ref) : undefined;
  const citationClaim = citationEntry?.claimId
    ? model.claimsById.get(citationEntry.claimId)
    : undefined;

  return (
    <ReportProvider value={context}>
      <ReportTitleBar model={model} />
      <ReportStrip sections={sections} active={activeSection} onNavigate={goToSection} />

      <div className="mx-auto w-full max-w-[1600px] px-6 pb-24 pt-6">
        <div className="flex gap-8">
          <ReportRail sections={sections} active={activeSection} onNavigate={goToSection} />

          <div className="min-w-0 flex-1 space-y-10">
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
                matched by deterministic lexical rules; figures were not interpreted and evidence
                was not semantically adjudicated. Re-run with a provider configured before relying
                on this.
              </Callout>
            )}

            <OverviewSection />
            <ExecutiveSummarySection />
            <ScoreBreakdownSection />
            <AnalysisSection />
            <RisksSection />
            <ClaimsSection request={claimFilter} />
            <EvidenceSection />
            <QuestionsSection />
            <AppendixSection />
          </div>
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
 * The persistent context bar: which company, what the answer was, and how to
 * take it away. Stays visible while the reader moves through the report.
 */
function ReportTitleBar({ model }: { model: ReportModel }) {
  const tone = recommendationTone(model.recommendation);

  return (
    <div className="sticky top-0 z-30 border-b border-line bg-canvas/85 backdrop-blur">
      <div className="mx-auto flex min-h-14 w-full max-w-[1600px] flex-wrap items-center gap-x-4 gap-y-2 px-6 py-2">
        <div className="flex min-w-0 flex-1 items-center gap-3">
          <Link
            href={`/documents/${model.documentId}`}
            className="shrink-0 text-fg-3 transition-colors hover:text-fg"
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
