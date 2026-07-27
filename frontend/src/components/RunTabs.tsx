'use client';

import { useState, type ReactNode } from 'react';

export interface Tab {
  id: string;
  label: string;
  count?: number;
  content: ReactNode;
}

export function RunTabs({ tabs, initial }: { tabs: Tab[]; initial?: string }) {
  const [active, setActive] = useState(initial ?? tabs[0]?.id ?? '');
  const current = tabs.find((tab) => tab.id === active) ?? tabs[0];

  return (
    <div>
      <div
        role="tablist"
        aria-label="Analysis sections"
        className="mb-5 flex flex-wrap gap-1 border-b border-ink-200 dark:border-ink-800"
      >
        {tabs.map((tab) => (
          <button
            key={tab.id}
            role="tab"
            id={`tab-${tab.id}`}
            aria-selected={tab.id === active}
            aria-controls={`panel-${tab.id}`}
            onClick={() => setActive(tab.id)}
            className={`-mb-px border-b-2 px-3.5 py-2 text-sm transition-colors ${
              tab.id === active
                ? 'border-ink-900 font-medium text-ink-900 dark:border-ink-100 dark:text-ink-100'
                : 'border-transparent text-ink-500 hover:text-ink-800 dark:hover:text-ink-200'
            }`}
          >
            {tab.label}
            {tab.count !== undefined && (
              <span className="ml-1.5 rounded bg-ink-200 px-1.5 py-0.5 text-[11px] tabular-nums text-ink-600 dark:bg-ink-800 dark:text-ink-400">
                {tab.count}
              </span>
            )}
          </button>
        ))}
      </div>

      <div role="tabpanel" id={`panel-${current?.id}`} aria-labelledby={`tab-${current?.id}`}>
        {current?.content}
      </div>
    </div>
  );
}
