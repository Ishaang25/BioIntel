'use client';

import { useState } from 'react';

import { cleanQuote, truncate } from '@/lib/format';
import { EVIDENCE_STATE, type CitationEntry, type ClaimRow } from '@/lib/report-model';
import { Portal, useDismiss, useFloatingPosition } from '@/components/ui/Floating';
import { Badge, StanceBadge } from '@/components/ui/primitives';
import type { EvidenceLink } from '@/lib/types';

export interface CitationAnchor {
  ref: string;
  element: HTMLElement;
  pinned: boolean;
}

/**
 * What a reference marker actually stands for, without leaving the page.
 *
 * Report citations point at extracted claims; each claim in turn points at the
 * literature records that were adjudicated against it. The popover shows both
 * levels so a reader can judge a sentence's support in place, and offers the
 * jump into the full claim record when they cannot.
 */
export function CitationPopover({
  anchor,
  entry,
  claim,
  onClose,
  onOpenClaim,
}: {
  anchor: CitationAnchor;
  entry: CitationEntry | undefined;
  claim: ClaimRow | undefined;
  onClose: () => void;
  onOpenClaim: (claimId: string) => void;
}) {
  const [layer, setLayer] = useState<HTMLDivElement | null>(null);
  const position = useFloatingPosition(anchor.element, layer, true, 8);

  useDismiss(anchor.pinned, onClose, [layer, anchor.element]);

  if (!entry) return null;

  const best = pickEvidence(claim?.evidenceLinks ?? []);
  const state = claim ? EVIDENCE_STATE[claim.state] : null;

  return (
    <Portal>
      <div
        ref={setLayer}
        role="dialog"
        aria-label={`Reference ${entry.ref}`}
        style={{
          position: 'fixed',
          top: position?.top ?? -9999,
          left: position?.left ?? -9999,
          width: 380,
          visibility: position ? 'visible' : 'hidden',
        }}
        className="z-50 overflow-hidden rounded-card border border-line bg-panel shadow-pop"
        onMouseEnter={() => undefined}
        onMouseLeave={() => (anchor.pinned ? undefined : onClose())}
      >
        <div className="flex items-center gap-2 border-b border-line bg-subtle px-3.5 py-2">
          <span className="num rounded border border-line bg-panel px-1.5 py-px font-mono text-2xs font-medium text-fg-2">
            {entry.ref}
          </span>
          <span className="label">
            {entry.kind === 'evidence' ? 'Literature record' : 'Extracted claim'}
          </span>
          {entry.pageNumber !== null && (
            <span className="ml-auto text-2xs text-fg-3">Deck p.{entry.pageNumber}</span>
          )}
        </div>

        <div className="max-h-[22rem] overflow-y-auto px-3.5 py-3">
          <p className="text-[13px] font-medium leading-snug text-fg">{entry.title}</p>

          {entry.quote && (
            <blockquote className="mt-2 border-l-2 border-line-strong pl-2.5 text-[12.5px] italic leading-relaxed text-fg-2">
              “{truncate(cleanQuote(entry.quote), 240)}”
            </blockquote>
          )}

          <div className="mt-2.5 flex flex-wrap items-center gap-1.5">
            {state && (
              <Badge tone={state.tone} title={state.description}>
                {state.label}
              </Badge>
            )}
            {claim?.score !== null && claim?.score !== undefined && (
              <Badge>Credibility {claim.score.toFixed(0)}</Badge>
            )}
            {entry.source && <Badge>{entry.source}</Badge>}
            {entry.pmid && <Badge>PMID {entry.pmid}</Badge>}
            {entry.nctId && <Badge>{entry.nctId}</Badge>}
          </div>

          {entry.abstract && (
            <p className="mt-2.5 text-[12.5px] leading-relaxed text-fg-2">
              {truncate(entry.abstract, 320)}
            </p>
          )}

          {best && <EvidencePreview link={best} />}

          {!best && claim && claim.evidenceLinks.length === 0 && (
            <p className="mt-3 border-t border-line pt-2.5 text-2xs leading-relaxed text-fg-3">
              {state?.description ??
                'No external literature was matched to this claim during retrieval.'}
            </p>
          )}
        </div>

        {claim && (
          <div className="border-t border-line px-3.5 py-2">
            <button
              type="button"
              className="btn btn-sm btn-ghost -ml-2.5"
              onClick={() => {
                onOpenClaim(claim.id);
                onClose();
              }}
            >
              Open full claim record →
            </button>
          </div>
        )}
      </div>
    </Portal>
  );
}

/** The most decision-relevant link: contradicting first, then strongest support. */
function pickEvidence(links: EvidenceLink[]): EvidenceLink | null {
  if (links.length === 0) return null;
  const contradicting = links.filter((link) => link.stance === 'contradicts');
  const pool = contradicting.length > 0 ? contradicting : links;
  return [...pool].sort((a, b) => b.strength * b.relevance - a.strength * a.relevance)[0] ?? null;
}

function EvidencePreview({ link }: { link: EvidenceLink }) {
  const record = link.evidence;
  return (
    <div className="mt-3 border-t border-line pt-2.5">
      <div className="mb-1.5 flex flex-wrap items-center gap-1.5">
        <StanceBadge stance={link.stance} />
        {record.pmid && <Badge>PMID {record.pmid}</Badge>}
        {record.nct_id && <Badge>{record.nct_id}</Badge>}
        {record.is_retracted && <Badge tone="crit">Retracted</Badge>}
        {record.is_preprint && <Badge tone="warn">Preprint</Badge>}
      </div>
      {record.url ? (
        <a
          href={record.url}
          target="_blank"
          rel="noreferrer noopener"
          className="text-[12.5px] font-medium leading-snug text-fg hover:underline"
        >
          {record.title}
        </a>
      ) : (
        <span className="text-[12.5px] font-medium leading-snug text-fg">{record.title}</span>
      )}
      <div className="mt-1 text-2xs text-fg-3">
        {[record.journal, record.publication_year, record.source].filter(Boolean).join(' · ')}
      </div>
      {link.rationale && (
        <p className="mt-1.5 text-[12.5px] leading-relaxed text-fg-2">
          {truncate(link.rationale, 260)}
        </p>
      )}
      {!link.rationale && record.abstract && (
        <p className="mt-1.5 text-[12.5px] leading-relaxed text-fg-2">
          {truncate(record.abstract, 240)}
        </p>
      )}
    </div>
  );
}
