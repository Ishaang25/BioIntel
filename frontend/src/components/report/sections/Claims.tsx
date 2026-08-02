'use client';

import { memo, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useVirtualizer } from '@tanstack/react-virtual';

import { humanise } from '@/lib/format';
import {
  EVIDENCE_STATE,
  EVIDENCE_STATE_ORDER,
  IMPORTANCE_TIER_LABEL,
  SCORE_EFFECT_META,
  SEVERITY_ORDER,
  SEVERITY_TONE,
  type ClaimRow,
} from '@/lib/report-model';
import type { RiskSeverity } from '@/lib/types';
import { Badge, Card, Dot, cx, toneClasses } from '@/components/ui/primitives';
import { useReport } from '../context';
import { Section } from './Section';

export interface ClaimFilterRequest {
  token: number;
  state?: string;
  type?: string;
  category?: string;
  severity?: string;
}

type SortKey = 'importance' | 'score' | 'page' | 'state';
type SortDirection = 'asc' | 'desc';

const ROW_HEIGHT = 46;

const STATE_RANK = new Map(EVIDENCE_STATE_ORDER.map((state, index) => [state, index]));

/**
 * The claim explorer.
 *
 * A dense, sortable, filterable table — one row per extracted claim — that acts
 * as the index into everything else. The row is deliberately terse; the whole
 * record lives one click away in the drawer, which keeps 300 claims scannable.
 */
export function ClaimsSection({ request }: { request: ClaimFilterRequest }) {
  const { model, openClaim } = useReport();

  const [query, setQuery] = useState('');
  const [type, setType] = useState('all');
  const [category, setCategory] = useState('all');
  const [state, setState] = useState('all');
  const [severity, setSeverity] = useState('all');
  const [criticalOnly, setCriticalOnly] = useState(false);
  const [sort, setSort] = useState<SortKey>('importance');
  const [direction, setDirection] = useState<SortDirection>('desc');

  // Filters seeded from elsewhere in the report (a chart segment, a metric
  // card) arrive as a token-stamped request so repeat clicks still apply.
  useEffect(() => {
    if (request.token === 0) return;
    setState(request.state ?? 'all');
    setType(request.type ?? 'all');
    setCategory(request.category ?? 'all');
    setSeverity(request.severity ?? 'all');
    setQuery('');
    setCriticalOnly(false);
  }, [request]);

  const categoriesPresent = useMemo(
    () => [...new Set(model.claims.map((row) => row.categoryKey))].sort(),
    [model.claims],
  );

  const statesPresent = useMemo(() => {
    const present = new Set(model.claims.map((row) => row.state));
    return EVIDENCE_STATE_ORDER.filter((value) => present.has(value));
  }, [model.claims]);

  const severitiesPresent = useMemo(() => {
    const present = new Set(
      model.claims.map((row) => row.riskSeverity).filter((value): value is RiskSeverity => !!value),
    );
    return SEVERITY_ORDER.filter((value) => present.has(value));
  }, [model.claims]);

  const rows = useMemo(() => {
    const needle = query.trim().toLowerCase();
    const filtered = model.claims.filter((row) => {
      if (criticalOnly && !row.claim.is_thesis_critical) return false;
      if (type !== 'all' && row.typeKey !== type) return false;
      if (category !== 'all' && row.categoryKey !== category) return false;
      if (state !== 'all' && row.state !== state) return false;
      if (severity !== 'all' && row.riskSeverity !== severity) return false;
      if (!needle) return true;
      return row.haystack.includes(needle);
    });

    const sign = direction === 'asc' ? 1 : -1;
    return filtered.sort((a, b) => {
      switch (sort) {
        case 'page':
          return sign * (a.page - b.page);
        case 'score':
          return sign * ((a.score ?? -1) - (b.score ?? -1));
        case 'state':
          return sign * ((STATE_RANK.get(b.state) ?? 99) - (STATE_RANK.get(a.state) ?? 99));
        default: {
          if (a.claim.is_thesis_critical !== b.claim.is_thesis_critical) {
            return a.claim.is_thesis_critical ? -1 : 1;
          }
          return sign * (a.importance - b.importance);
        }
      }
    });
  }, [model.claims, query, type, category, state, severity, criticalOnly, sort, direction]);

  // The table scrolls inside its own viewport rather than growing the page:
  // it keeps the column header pinned, keeps the rest of the report reachable,
  // and lets the virtualiser measure against a stable element.
  const scrollRef = useRef<HTMLDivElement>(null);
  const virtualizer = useVirtualizer({
    count: rows.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => ROW_HEIGHT,
    overscan: 10,
  });

  // A filter change can leave the viewport scrolled past the new, shorter list.
  useEffect(() => {
    scrollRef.current?.scrollTo({ top: 0 });
  }, [query, type, category, state, severity, criticalOnly]);

  const clearFilters = useCallback(() => {
    setQuery('');
    setType('all');
    setCategory('all');
    setState('all');
    setSeverity('all');
    setCriticalOnly(false);
  }, []);

  const onSort = useCallback(
    (key: SortKey) => {
      if (key === sort) setDirection((value) => (value === 'asc' ? 'desc' : 'asc'));
      else {
        setSort(key);
        setDirection(key === 'page' ? 'asc' : 'desc');
      }
    },
    [sort],
  );

  const filtered = rows.length !== model.claims.length;

  return (
    <Section
      id="claims"
      title="Claim explorer"
      description="Every scientific claim extracted from the deck, with its verbatim source, what the outside record said about it, and how it moved the score."
      actions={
        <span className="num text-[13px] text-fg-3">
          {rows.length}
          {filtered && <span className="text-fg-3"> of {model.claims.length}</span>} claims
        </span>
      }
    >
      <Card className="overflow-hidden">
        {/* ------------------------------------------------------ filters --- */}
        <div className="flex flex-wrap items-center gap-2 border-b border-line p-3">
          <div className="relative min-w-[15rem] flex-1">
            <svg
              width="13"
              height="13"
              viewBox="0 0 14 14"
              aria-hidden
              className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-fg-3"
            >
              <circle cx="6" cy="6" r="4.25" fill="none" stroke="currentColor" strokeWidth="1.5" />
              <path d="M9.2 9.2L13 13" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
            </svg>
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Search claims, quotes, entities…"
              aria-label="Search claims"
              className="field-sm pl-8"
            />
          </div>

          <Select
            value={type}
            onChange={setType}
            label="Claim type"
            options={[
              { value: 'all', label: 'All claim types' },
              ...model.claimTypes.map((entry) => ({ value: entry.key, label: entry.label })),
            ]}
          />
          <Select
            value={category}
            onChange={setCategory}
            label="Category"
            options={[
              { value: 'all', label: 'All categories' },
              ...categoriesPresent.map((value) => ({ value, label: humanise(value) })),
            ]}
          />
          <Select
            value={state}
            onChange={setState}
            label="Evidence state"
            options={[
              { value: 'all', label: 'All evidence states' },
              ...statesPresent.map((value) => ({
                value,
                label: EVIDENCE_STATE[value].label,
              })),
            ]}
          />
          {severitiesPresent.length > 0 && (
            <Select
              value={severity}
              onChange={setSeverity}
              label="Risk severity"
              options={[
                { value: 'all', label: 'Any risk severity' },
                ...severitiesPresent.map((value) => ({ value, label: `${humanise(value)} risk` })),
              ]}
            />
          )}

          <label className="flex cursor-pointer select-none items-center gap-1.5 rounded-md px-2 py-1.5 text-[13px] text-fg-2 hover:bg-subtle hover:text-fg">
            <input
              type="checkbox"
              checked={criticalOnly}
              onChange={(event) => setCriticalOnly(event.target.checked)}
              className="h-3.5 w-3.5 accent-current"
            />
            Thesis-critical
          </label>

          {(filtered || query) && (
            <button
              type="button"
              className="btn btn-sm btn-ghost"
              onClick={() => {
                setQuery('');
                setType('all');
                setCategory('all');
                setState('all');
                setSeverity('all');
                setCriticalOnly(false);
              }}
            >
              Reset
            </button>
          )}
        </div>

        {/* -------------------------------------------------------- table --- */}
        <div
          ref={scrollRef}
          role="table"
          aria-label="Extracted claims"
          aria-rowcount={rows.length}
          className="max-h-[min(72vh,760px)] overflow-y-auto overscroll-contain"
        >
          <div
            role="row"
            className="sticky top-0 z-10 flex items-center gap-3 border-b border-line bg-panel/95 px-4 py-2 backdrop-blur"
          >
            <HeaderCell className="min-w-0 flex-1">Claim</HeaderCell>
            <HeaderCell className="hidden w-[132px] shrink-0 xl:block">Type</HeaderCell>
            <HeaderCell
              className="w-[128px] shrink-0"
              sortKey="state"
              sort={sort}
              direction={direction}
              onSort={onSort}
            >
              Evidence state
            </HeaderCell>
            <HeaderCell
              className="hidden w-[104px] shrink-0 lg:block"
              sortKey="score"
              sort={sort}
              direction={direction}
              onSort={onSort}
              align="right"
            >
              Score effect
            </HeaderCell>
            <HeaderCell
              className="hidden w-[92px] shrink-0 lg:block"
              sortKey="importance"
              sort={sort}
              direction={direction}
              onSort={onSort}
            >
              Importance
            </HeaderCell>
            <HeaderCell
              className="w-[44px] shrink-0"
              sortKey="page"
              sort={sort}
              direction={direction}
              onSort={onSort}
              align="right"
            >
              Page
            </HeaderCell>
            <HeaderCell className="hidden w-[128px] shrink-0 xl:block">Status</HeaderCell>
          </div>

          {rows.length === 0 ? (
            /* Two different situations that used to read as one. "Nothing
               matched your filter" is the reader's doing and is fixed by a
               click; "the deck yielded no claims" is a finding about the run
               and no filter change will help. */
            model.claims.length === 0 ? (
              <div className="px-4 py-16 text-center">
                <p className="text-sm font-medium text-fg">No claims were extracted</p>
                <p className="mx-auto mt-1 max-w-md text-[13px] leading-relaxed text-fg-2">
                  The extraction stage found nothing it could anchor to a page. That usually means
                  the deck is mostly imagery with little asserted science, or the parse recovered
                  very little text — the Appendix records what was read.
                </p>
              </div>
            ) : (
              <div className="px-4 py-16 text-center">
                <p className="text-sm font-medium text-fg">No claims match these filters</p>
                <p className="mt-1 text-[13px] text-fg-2">
                  {model.claims.length} claims were extracted; none of them match.
                </p>
                <button type="button" className="btn btn-sm btn-secondary mt-3" onClick={clearFilters}>
                  Clear all filters
                </button>
              </div>
            )
          ) : (
            <div style={{ height: virtualizer.getTotalSize(), position: 'relative' }}>
              {virtualizer.getVirtualItems().map((item) => {
                const row = rows[item.index];
                if (!row) return null;
                return (
                  <div
                    key={row.id}
                    data-index={item.index}
                    style={{
                      position: 'absolute',
                      top: 0,
                      left: 0,
                      width: '100%',
                      transform: `translateY(${item.start}px)`,
                    }}
                  >
                    <ClaimTableRow row={row} onOpen={openClaim} />
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </Card>
    </Section>
  );
}

/* ---------------------------------------------------------------------- row --- */

/**
 * Memoised: rows are pure in their claim, and `onOpen` is stable, so a filter
 * or sort change re-renders only the rows whose identity actually changed.
 */
const ClaimTableRow = memo(function ClaimTableRow({
  row,
  onOpen,
}: {
  row: ClaimRow;
  onOpen: (id: string) => void;
}) {
  const state = EVIDENCE_STATE[row.state];
  const effect = SCORE_EFFECT_META[row.effect];

  return (
    <div
      role="row"
      tabIndex={0}
      onClick={() => onOpen(row.id)}
      onKeyDown={(event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          onOpen(row.id);
        }
      }}
      style={{ height: ROW_HEIGHT }}
      className="flex cursor-pointer items-center gap-3 border-b border-line px-4 text-[13px] transition-colors last:border-b-0 hover:bg-subtle"
    >
      <div role="cell" className="flex min-w-0 flex-1 items-center gap-2">
        {row.claim.is_thesis_critical && (
          <span
            aria-label="Thesis-critical"
            title="Thesis-critical"
            className="h-3.5 w-[2px] shrink-0 rounded-full bg-info"
          />
        )}
        <span className="truncate text-fg" title={row.statement}>
          {row.statement}
        </span>
      </div>

      <div role="cell" className="hidden w-[132px] shrink-0 truncate text-fg-2 xl:block">
        {row.typeLabel}
      </div>

      <div role="cell" className="flex w-[128px] shrink-0 items-center gap-1.5">
        <Dot tone={state.tone} />
        <span className="truncate text-fg-2" title={state.description}>
          {state.short}
        </span>
      </div>

      <div
        role="cell"
        className="hidden w-[104px] shrink-0 items-center justify-end gap-1.5 lg:flex"
      >
        {row.score === null ? (
          <span className="text-2xs text-fg-3">Excluded</span>
        ) : (
          <>
            <span className={cx('num text-2xs', toneClasses.text[effect.tone])}>
              {row.effectDelta !== null && row.effectDelta !== 0
                ? `${row.effectDelta > 0 ? '+' : ''}${row.effectDelta.toFixed(0)}`
                : '±0'}
            </span>
            <span className="num w-6 text-right font-medium text-fg">{row.score.toFixed(0)}</span>
          </>
        )}
      </div>

      <div role="cell" className="hidden w-[92px] shrink-0 items-center gap-2 lg:flex">
        <div className="h-1 flex-1 overflow-hidden rounded-full bg-subtle">
          <div
            className={cx(
              'h-full rounded-full',
              row.importanceTier === 'critical' ? 'bg-info' : 'bg-fg-3',
            )}
            style={{ width: `${Math.round(row.importance * 100)}%` }}
          />
        </div>
        <span className="num w-6 shrink-0 text-right text-2xs text-fg-3">
          {Math.round(row.importance * 100)}
        </span>
      </div>

      <div role="cell" className="num w-[44px] shrink-0 text-right text-fg-3">
        {row.page}
      </div>

      <div role="cell" className="hidden w-[128px] shrink-0 xl:block">
        <StatusCell row={row} />
      </div>
    </div>
  );
});

function StatusCell({ row }: { row: ClaimRow }) {
  if (row.riskSeverity) {
    return (
      <Badge tone={SEVERITY_TONE[row.riskSeverity]} title={`${row.risks.length} linked finding(s)`}>
        {humanise(row.riskSeverity)} risk
      </Badge>
    );
  }
  const flag = row.flags.find((entry) => entry.key !== 'critical');
  if (flag) {
    return (
      <Badge tone={flag.tone} title={flag.title}>
        {flag.label}
      </Badge>
    );
  }
  if (row.claim.is_thesis_critical) {
    return <Badge tone="info">{IMPORTANCE_TIER_LABEL.critical}</Badge>;
  }
  return <span className="text-2xs text-fg-3">—</span>;
}

/* ------------------------------------------------------------------ header --- */

function HeaderCell({
  children,
  className,
  sortKey,
  sort,
  direction,
  onSort,
  align = 'left',
}: {
  children: React.ReactNode;
  className?: string;
  sortKey?: SortKey;
  sort?: SortKey;
  direction?: SortDirection;
  onSort?: (key: SortKey) => void;
  align?: 'left' | 'right';
}) {
  const active = sortKey !== undefined && sortKey === sort;
  const content = (
    <>
      {children}
      {sortKey && (
        <span
          aria-hidden
          className={cx('ml-1 inline-block text-[8px]', active ? 'text-fg' : 'text-transparent')}
        >
          {active && direction === 'asc' ? '▲' : '▼'}
        </span>
      )}
    </>
  );

  if (!sortKey || !onSort) {
    return (
      <div
        role="columnheader"
        className={cx('label', align === 'right' && 'text-right', className)}
      >
        {content}
      </div>
    );
  }

  return (
    <div role="columnheader" aria-sort={active ? (direction === 'asc' ? 'ascending' : 'descending') : 'none'} className={className}>
      <button
        type="button"
        onClick={() => onSort(sortKey)}
        className={cx(
          'label group flex w-full items-center transition-colors hover:text-fg-2',
          align === 'right' && 'justify-end',
        )}
      >
        {content}
      </button>
    </div>
  );
}

/* ------------------------------------------------------------------ select --- */

function Select({
  value,
  onChange,
  options,
  label,
}: {
  value: string;
  onChange: (value: string) => void;
  options: Array<{ value: string; label: string }>;
  label: string;
}) {
  return (
    <select
      value={value}
      onChange={(event) => onChange(event.target.value)}
      aria-label={label}
      className="field-sm w-auto max-w-[13rem]"
    >
      {options.map((option) => (
        <option key={option.value} value={option.value}>
          {option.label}
        </option>
      ))}
    </select>
  );
}
