'use client';

import { useEffect, useRef } from 'react';

import { cx } from '@/components/ui/primitives';
import { VIEWS, type ViewId } from './navigation';

export interface NavCounts {
  sections: number;
  risks: number;
  claims: number;
  evidence: number;
  questions: number;
}

/**
 * The report's primary navigation.
 *
 * A tab bar rather than a table of contents down the side: with the report
 * split into views there is one destination per tab, so the control that
 * changes what you are looking at should look like a control, not like an
 * outline of a document you are already inside.
 *
 * Keyboard behaviour follows the tablist pattern — arrows move between tabs,
 * Home and End jump to the ends — because a reader working through a memo has
 * their hands on the keyboard, not the trackpad.
 */
export function ReportNav({
  active,
  counts,
  onSelect,
}: {
  active: ViewId;
  counts: NavCounts;
  onSelect: (view: ViewId) => void;
}) {
  const listRef = useRef<HTMLDivElement>(null);

  // Keep the selected tab in view on narrow screens, where the bar scrolls.
  useEffect(() => {
    listRef.current
      ?.querySelector<HTMLElement>('[aria-selected="true"]')
      ?.scrollIntoView({ block: 'nearest', inline: 'nearest' });
  }, [active]);

  function onKeyDown(event: React.KeyboardEvent) {
    const index = VIEWS.findIndex((view) => view.id === active);
    let next = index;
    if (event.key === 'ArrowRight') next = (index + 1) % VIEWS.length;
    else if (event.key === 'ArrowLeft') next = (index - 1 + VIEWS.length) % VIEWS.length;
    else if (event.key === 'Home') next = 0;
    else if (event.key === 'End') next = VIEWS.length - 1;
    else return;

    event.preventDefault();
    const target = VIEWS[next]!;
    onSelect(target.id);
    requestAnimationFrame(() => {
      listRef.current?.querySelector<HTMLElement>(`#report-tab-${target.id}`)?.focus();
    });
  }

  return (
    <div className="sticky top-14 z-20 border-b border-line bg-canvas/95 backdrop-blur">
      <div
        ref={listRef}
        role="tablist"
        aria-label="Report views"
        onKeyDown={onKeyDown}
        className="mx-auto flex max-w-[1600px] gap-0.5 overflow-x-auto px-4 sm:px-6"
      >
        {VIEWS.map((view, index) => {
          const selected = view.id === active;
          const count = view.countKey ? counts[view.countKey] : undefined;
          // A hairline before the first tab of each group does the work a
          // heading would, without spending a row on it.
          const startsGroup = index > 0 && VIEWS[index - 1]!.group !== view.group;
          return (
            <div key={view.id} className={cx('flex items-center', startsGroup && 'gap-0.5')}>
              {startsGroup && <span aria-hidden className="mx-1.5 h-4 w-px bg-line" />}
              <button
                type="button"
                role="tab"
                id={`report-tab-${view.id}`}
                aria-selected={selected}
                aria-controls={`report-panel-${view.id}`}
                tabIndex={selected ? 0 : -1}
                onClick={() => onSelect(view.id)}
                className={cx(
                  'relative whitespace-nowrap rounded-t-md px-3 py-2.5 text-[13px] transition-colors',
                  'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-fg/30',
                  selected ? 'font-medium text-fg' : 'text-fg-2 hover:text-fg',
                )}
              >
                {view.label}
                {count !== undefined && count > 0 && (
                  <span className="num ml-1.5 text-2xs text-fg-3">{count}</span>
                )}
                <span
                  aria-hidden
                  className={cx(
                    'absolute inset-x-2 -bottom-px h-0.5 rounded-full transition-colors',
                    selected ? 'bg-fg' : 'bg-transparent',
                  )}
                />
              </button>
            </div>
          );
        })}
      </div>
    </div>
  );
}
