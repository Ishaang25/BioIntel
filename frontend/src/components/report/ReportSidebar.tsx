'use client';

import { useEffect, useState } from 'react';

import { cx } from '@/components/ui/primitives';

export interface NavSection {
  id: string;
  label: string;
  count?: number;
}

/**
 * Tracks which section owns the viewport.
 *
 * Uses a top-biased root margin so a heading becomes "current" as it reaches
 * the upper third, which matches how people read, rather than when it happens
 * to cross the exact centre line.
 */
export function useScrollSpy(ids: string[]): string {
  const [active, setActive] = useState(ids[0] ?? '');

  useEffect(() => {
    const elements = ids
      .map((id) => document.getElementById(id))
      .filter((node): node is HTMLElement => node !== null);
    if (elements.length === 0) return;

    const visible = new Set<string>();
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) visible.add(entry.target.id);
          else visible.delete(entry.target.id);
        }
        const first = ids.find((id) => visible.has(id));
        if (first) setActive(first);
      },
      { rootMargin: '-88px 0px -55% 0px', threshold: 0 },
    );

    for (const element of elements) observer.observe(element);
    return () => observer.disconnect();
    // `ids` is rebuilt only when the report changes shape.
  }, [ids]);

  return active;
}

/** Laptop and desktop: a persistent rail beside the report. */
export function ReportRail({
  sections,
  active,
  onNavigate,
}: {
  sections: NavSection[];
  active: string;
  onNavigate: (id: string) => void;
}) {
  return (
    <nav
      aria-label="Report sections"
      className="sticky top-[4.5rem] hidden max-h-[calc(100vh-6rem)] shrink-0 overflow-y-auto pb-6 lg:block lg:w-[196px] xl:w-[212px]"
    >
      <ul className="space-y-px">
        {sections.map((section) => (
          <li key={section.id}>
            <button
              type="button"
              onClick={() => onNavigate(section.id)}
              aria-current={active === section.id ? 'true' : undefined}
              className={cx(
                'flex w-full items-center gap-2.5 rounded-md py-1.5 pl-2.5 pr-2 text-left text-[13px] transition-colors',
                active === section.id
                  ? 'bg-subtle font-medium text-fg'
                  : 'text-fg-2 hover:bg-subtle/60 hover:text-fg',
              )}
            >
              <span
                aria-hidden
                className={cx(
                  'h-3.5 w-px shrink-0 rounded-full transition-colors',
                  active === section.id ? 'bg-fg' : 'bg-line-strong',
                )}
              />
              <span className="min-w-0 flex-1 truncate">{section.label}</span>
              {section.count !== undefined && (
                <span className="num text-2xs text-fg-3">{section.count}</span>
              )}
            </button>
          </li>
        ))}
      </ul>
    </nav>
  );
}

/** Tablet: the same map, collapsed into a scrolling strip under the title bar. */
export function ReportStrip({
  sections,
  active,
  onNavigate,
}: {
  sections: NavSection[];
  active: string;
  onNavigate: (id: string) => void;
}) {
  return (
    <nav
      aria-label="Report sections"
      className="sticky top-14 z-20 border-b border-line bg-canvas/95 backdrop-blur lg:hidden"
    >
      <ul className="mx-auto flex max-w-[1600px] gap-1 overflow-x-auto px-6 py-2">
        {sections.map((section) => (
          <li key={section.id}>
            <button
              type="button"
              onClick={() => onNavigate(section.id)}
              aria-current={active === section.id ? 'true' : undefined}
              className={cx(
                'whitespace-nowrap rounded-md px-2.5 py-1.5 text-[13px] transition-colors',
                active === section.id
                  ? 'bg-subtle font-medium text-fg'
                  : 'text-fg-2 hover:text-fg',
              )}
            >
              {section.label}
              {section.count !== undefined && (
                <span className="num ml-1.5 text-2xs text-fg-3">{section.count}</span>
              )}
            </button>
          </li>
        ))}
      </ul>
    </nav>
  );
}
