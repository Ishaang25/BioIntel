'use client';

import { useMemo, useState } from 'react';

import { CorroborationChip } from '@/components/ScorecardView';
import { BandChip, Chip, StanceChip, humanise } from '@/components/ui';
import type { Claim, EvidenceLink } from '@/lib/types';

type SortKey = 'importance' | 'credibility' | 'page';

/**
 * The claims workspace.
 *
 * Every row shows the claim, its verbatim source quote, and the evidence that
 * bears on it. The quote is always visible rather than hidden behind a click:
 * an analyst's first question about any extracted claim is "where does it
 * actually say that?".
 */
export function ClaimExplorer({
  claims,
  evidenceByClaim,
}: {
  claims: Claim[];
  evidenceByClaim: Record<string, EvidenceLink[]>;
}) {
  const [query, setQuery] = useState('');
  const [category, setCategory] = useState<string>('all');
  const [criticalOnly, setCriticalOnly] = useState(false);
  const [contestedOnly, setContestedOnly] = useState(false);
  const [sort, setSort] = useState<SortKey>('importance');
  const [expanded, setExpanded] = useState<string | null>(null);

  const categories = useMemo(
    () => Array.from(new Set(claims.map((c) => c.category))).sort(),
    [claims],
  );

  const visible = useMemo(() => {
    const needle = query.trim().toLowerCase();
    const filtered = claims.filter((claim) => {
      if (criticalOnly && !claim.is_thesis_critical) return false;
      if (category !== 'all' && claim.category !== category) return false;
      if (
        contestedOnly &&
        claim.assessment?.corroboration_status !== 'contradicted' &&
        claim.assessment?.corroboration_status !== 'disputed'
      ) {
        return false;
      }
      if (!needle) return true;
      return (
        claim.statement.toLowerCase().includes(needle) ||
        claim.verbatim_quote.toLowerCase().includes(needle) ||
        claim.entities.some((entity) => entity.name.toLowerCase().includes(needle))
      );
    });

    return filtered.sort((a, b) => {
      if (sort === 'page') return a.page_number - b.page_number;
      if (sort === 'credibility') {
        return (a.assessment?.credibility_score ?? 0) - (b.assessment?.credibility_score ?? 0);
      }
      if (a.is_thesis_critical !== b.is_thesis_critical) return a.is_thesis_critical ? -1 : 1;
      return b.importance - a.importance;
    });
  }, [claims, query, category, criticalOnly, contestedOnly, sort]);

  return (
    <div>
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <input
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Search claims, quotes and entities…"
          aria-label="Search claims"
          className="min-w-[16rem] flex-1 rounded-md border border-ink-300 bg-white px-3 py-1.5 text-sm
                     placeholder:text-ink-400 focus:border-ink-500 focus:outline-none
                     dark:border-ink-700 dark:bg-ink-900"
        />
        <select
          value={category}
          onChange={(event) => setCategory(event.target.value)}
          aria-label="Filter by category"
          className="rounded-md border border-ink-300 bg-white px-2 py-1.5 text-sm dark:border-ink-700 dark:bg-ink-900"
        >
          <option value="all">All categories</option>
          {categories.map((value) => (
            <option key={value} value={value}>
              {humanise(value)}
            </option>
          ))}
        </select>
        <select
          value={sort}
          onChange={(event) => setSort(event.target.value as SortKey)}
          aria-label="Sort claims"
          className="rounded-md border border-ink-300 bg-white px-2 py-1.5 text-sm dark:border-ink-700 dark:bg-ink-900"
        >
          <option value="importance">Most important first</option>
          <option value="credibility">Weakest first</option>
          <option value="page">Deck order</option>
        </select>
        <label className="flex items-center gap-1.5 text-sm">
          <input
            type="checkbox"
            checked={criticalOnly}
            onChange={(event) => setCriticalOnly(event.target.checked)}
          />
          Thesis-critical
        </label>
        <label className="flex items-center gap-1.5 text-sm">
          <input
            type="checkbox"
            checked={contestedOnly}
            onChange={(event) => setContestedOnly(event.target.checked)}
          />
          Contradicted only
        </label>
      </div>

      <div className="mb-3 text-xs text-ink-500">
        Showing {visible.length} of {claims.length} claims
      </div>

      <div className="space-y-3">
        {visible.map((claim) => {
          const links = evidenceByClaim[claim.id] ?? [];
          const isOpen = expanded === claim.id;
          return (
            <article key={claim.id} className="card">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0 flex-1">
                  <p className="font-medium leading-snug">{claim.statement}</p>

                  <blockquote className="mt-2 border-l-2 border-ink-300 pl-3 text-sm italic text-ink-500 dark:border-ink-700">
                    “{claim.verbatim_quote}”
                    <span className="ml-2 not-italic text-xs text-ink-400">
                      — page {claim.page_number}
                      {claim.quote_verification === 'fuzzy' && ' (approximate match)'}
                      {claim.from_visual && ' · read from a figure'}
                    </span>
                  </blockquote>

                  <div className="mt-3 flex flex-wrap items-center gap-1.5">
                    {claim.assessment && (
                      <CorroborationChip status={claim.assessment.corroboration_status} />
                    )}
                    <Chip>{humanise(claim.claim_type)}</Chip>
                    <Chip>{humanise(claim.category)}</Chip>
                    <Chip>{humanise(claim.claimed_evidence_tier)}</Chip>
                    {claim.is_thesis_critical && <Chip tone="accent">thesis-critical</Chip>}
                    {claim.hedging_language && <Chip>hedged</Chip>}
                    {claim.needs_human_review && <Chip>needs review</Chip>}
                    {claim.entities.slice(0, 4).map((entity) => (
                      <Chip key={entity.id}>{entity.canonical_name ?? entity.name}</Chip>
                    ))}
                  </div>
                </div>

                {claim.assessment && claim.assessment.is_scorable && (
                  <div className="shrink-0 text-right">
                    <BandChip
                      band={claim.assessment.credibility_band}
                      score={claim.assessment.credibility_score}
                    />
                    <div className="mt-2 text-xs tabular-nums text-ink-500">
                      <span className="text-emerald-600 dark:text-emerald-400">
                        {claim.assessment.supporting_count} for
                      </span>
                      {' · '}
                      <span className="text-red-600 dark:text-red-400">
                        {claim.assessment.contradicting_count} against
                      </span>
                    </div>
                  </div>
                )}
              </div>

              {claim.assessment?.verdict && (
                <p className="mt-3 rounded-md bg-ink-100 px-3 py-2 text-sm text-ink-700 dark:bg-ink-800/60 dark:text-ink-300">
                  {claim.assessment.verdict}
                </p>
              )}

              {claim.assessment?.verification_detail && (
                <p className="mt-2 rounded-md border border-sky-500/25 bg-sky-500/5 px-3 py-2 text-sm">
                  <span className="label">
                    Checked against {humanise(claim.assessment.verification_source ?? 'source')}
                  </span>{' '}
                  {claim.assessment.verification_detail}
                </p>
              )}

              {claim.assessment?.score_explanation && (
                <details className="mt-2 text-sm">
                  <summary className="cursor-pointer text-ink-500 hover:text-ink-900 dark:hover:text-ink-100">
                    Why this score
                  </summary>
                  <p className="mt-1.5 text-ink-600 dark:text-ink-400">
                    {claim.assessment.score_explanation}
                  </p>
                </details>
              )}

              {links.length > 0 && (
                <>
                  <button
                    className="mt-3 text-sm text-ink-500 hover:text-ink-900 dark:hover:text-ink-100"
                    onClick={() => setExpanded(isOpen ? null : claim.id)}
                    aria-expanded={isOpen}
                  >
                    {isOpen ? 'Hide' : 'Show'} {links.length} evidence record
                    {links.length === 1 ? '' : 's'}
                  </button>
                  {isOpen && <EvidenceList links={links} />}
                </>
              )}

              {links.length === 0 && (
                <p className="mt-3 text-sm text-ink-500">
                  No external literature was matched to this claim.
                </p>
              )}

              {claim.review_reasons.length > 0 && (
                <ul className="mt-3 space-y-0.5 text-xs text-amber-700 dark:text-amber-500">
                  {claim.review_reasons.map((reason) => (
                    <li key={reason}>⚠ {reason}</li>
                  ))}
                </ul>
              )}
            </article>
          );
        })}
      </div>

      {visible.length === 0 && (
        <div className="card py-10 text-center text-sm text-ink-500">
          No claims match these filters.
        </div>
      )}
    </div>
  );
}

function EvidenceList({ links }: { links: EvidenceLink[] }) {
  return (
    <ul className="mt-3 space-y-3 border-t border-ink-200 pt-3 dark:border-ink-800">
      {links.map((link) => {
        const record = link.evidence;
        return (
          <li key={link.id} className="text-sm">
            <div className="flex flex-wrap items-center gap-2">
              <StanceChip stance={link.stance} />
              <Chip>{humanise(record.study_design)}</Chip>
              {record.is_retracted && <Chip>retracted</Chip>}
              {record.is_preprint && <Chip>preprint</Chip>}
              <span className="font-mono text-xs tabular-nums text-ink-400">
                rel {link.relevance.toFixed(2)} · str {link.strength.toFixed(2)}
              </span>
            </div>

            <div className="mt-1.5">
              {record.url ? (
                <a
                  href={record.url}
                  target="_blank"
                  rel="noreferrer noopener"
                  className="font-medium hover:underline"
                >
                  {record.title}
                </a>
              ) : (
                <span className="font-medium">{record.title}</span>
              )}
              <span className="ml-2 text-xs text-ink-500">
                {[record.journal, record.publication_year].filter(Boolean).join(' · ')}
                {record.pmid && ` · PMID:${record.pmid}`}
                {record.nct_id && ` · ${record.nct_id}`}
              </span>
            </div>

            {link.supporting_quote && (
              <blockquote className="mt-1.5 border-l-2 border-ink-300 pl-3 text-xs italic text-ink-500 dark:border-ink-700">
                “{link.supporting_quote}”
              </blockquote>
            )}
            {link.rationale && <p className="mt-1.5 text-xs text-ink-600 dark:text-ink-400">{link.rationale}</p>}
            {link.caveats.length > 0 && (
              <p className="mt-1 text-xs text-ink-500">Caveats: {link.caveats.join('; ')}</p>
            )}
          </li>
        );
      })}
    </ul>
  );
}
