'use client';

import type { ReactNode } from 'react';

/**
 * A top-level report section.
 *
 * Sections stay mounted and fully laid out. `content-visibility` was tried here
 * and removed: skipping layout leaves the scroll height an estimate, so jumping
 * to a section from the rail lands in the wrong place and the page reflows
 * under the reader. The expensive parts are handled where they actually are —
 * the claim list is virtualised and the appendix renders one tab at a time.
 */
export function Section({
  id,
  title,
  description,
  actions,
  children,
}: {
  id: string;
  title: string;
  description?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section id={id} aria-labelledby={`${id}-heading`} className="scroll-anchor">
      <div className="mb-4 flex flex-wrap items-end justify-between gap-x-4 gap-y-2">
        <div className="min-w-0">
          <h2
            id={`${id}-heading`}
            className="text-[17px] font-semibold tracking-[-0.01em] text-fg"
          >
            {title}
          </h2>
          {description && (
            <p className="mt-1 max-w-prose text-[13px] leading-relaxed text-fg-2">{description}</p>
          )}
        </div>
        {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
      </div>
      {children}
    </section>
  );
}
