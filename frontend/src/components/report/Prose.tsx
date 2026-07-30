'use client';

import { useMemo, useRef, type MouseEvent } from 'react';

import { linkCitations, markdownToHtml } from '@/lib/markdown';
import { cx } from '@/components/ui/primitives';
import { useReport } from './context';

/**
 * Report narrative with live citation tokens.
 *
 * The markdown is escaped before any markup is produced (see `lib/markdown`),
 * then `[C1]` markers are rewritten into real buttons. Rather than mounting one
 * React component per marker — a section can carry twenty — the container
 * delegates pointer events, so the cost is one listener per section regardless
 * of citation density.
 */
export function Prose({
  markdown,
  className,
}: {
  markdown: string;
  className?: string;
}) {
  const { model, openCitation, closeCitation } = useReport();
  const containerRef = useRef<HTMLDivElement>(null);

  const html = useMemo(() => {
    const refs = new Set(model.citationsByRef.keys());
    return linkCitations(markdownToHtml(markdown), refs);
  }, [markdown, model.citationsByRef]);

  const resolve = (event: MouseEvent<HTMLDivElement>): HTMLElement | null => {
    const target = event.target as HTMLElement | null;
    return target?.closest<HTMLElement>('[data-cite]') ?? null;
  };

  return (
    <div
      ref={containerRef}
      className={cx('prose-memo', className)}
      onClick={(event) => {
        const token = resolve(event);
        if (!token) return;
        event.preventDefault();
        openCitation(token.dataset.cite ?? '', token, true);
      }}
      onMouseOver={(event) => {
        const token = resolve(event);
        if (token) openCitation(token.dataset.cite ?? '', token, false);
      }}
      onMouseOut={(event) => {
        if (resolve(event)) closeCitation();
      }}
      dangerouslySetInnerHTML={{ __html: html }}
    />
  );
}

/**
 * Plain prose that preserves the paragraph breaks of a non-markdown field
 * (the executive summary arrives as free text) and still resolves citations.
 */
export function TextBlock({ text, className }: { text: string; className?: string }) {
  const paragraphs = useMemo(
    () => text.split(/\n{2,}/).map((part) => part.trim()).filter(Boolean),
    [text],
  );

  return (
    <div className={cx('space-y-3.5', className)}>
      {paragraphs.map((paragraph, index) => (
        <Prose key={index} markdown={paragraph} />
      ))}
    </div>
  );
}
