'use client';

import { useMemo, useState } from 'react';

import { firstSentence, humanise } from '@/lib/format';
import {
  EVIDENCE_STATE,
  SEVERITY_ORDER,
  SEVERITY_TONE,
  type RiskCard as RiskCardModel,
  type Tone,
} from '@/lib/report-model';
import type { RiskSeverity } from '@/lib/types';
import { Badge, Card, EmptyState, SeverityBadge, cx, toneClasses } from '@/components/ui/primitives';
import { useReport } from '../context';
import { Section } from './Section';

/**
 * Diligence findings as cards.
 *
 * Collapsed, a card is one sentence — enough to triage a list of twenty.
 * Expanded, it carries the full finding, why it matters, what it rests on and
 * the question to put to management, with a route back to the source claim.
 */
export function RisksSection() {
  const { model } = useReport();
  const [severity, setSeverity] = useState<RiskSeverity | 'all'>('all');
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  const counts = useMemo(() => {
    const tally = new Map<RiskSeverity, number>();
    for (const card of model.risks) tally.set(card.severity, (tally.get(card.severity) ?? 0) + 1);
    return tally;
  }, [model.risks]);

  const visible = useMemo(
    () => (severity === 'all' ? model.risks : model.risks.filter((c) => c.severity === severity)),
    [model.risks, severity],
  );

  const allExpanded = visible.length > 0 && visible.every((card) => expanded.has(card.risk.id));

  const toggle = (id: string) =>
    setExpanded((previous) => {
      const next = new Set(previous);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  return (
    <Section
      id="risks"
      title="Risk findings"
      description="Every finding the analysis raised, ordered by severity. Rule-based findings come from deterministic checks; the rest are drawn from the claim-level assessment."
      actions={
        <button
          type="button"
          className="btn btn-sm btn-secondary"
          onClick={() =>
            setExpanded(allExpanded ? new Set() : new Set(visible.map((c) => c.risk.id)))
          }
          disabled={visible.length === 0}
        >
          {allExpanded ? 'Collapse all' : 'Expand all'}
        </button>
      }
    >
      <div className="mb-3 flex flex-wrap gap-1">
        <FilterPill
          active={severity === 'all'}
          onClick={() => setSeverity('all')}
          label="All"
          count={model.risks.length}
        />
        {SEVERITY_ORDER.filter((level) => counts.has(level)).map((level) => (
          <FilterPill
            key={level}
            active={severity === level}
            onClick={() => setSeverity(level)}
            label={humanise(level)}
            count={counts.get(level) ?? 0}
            tone={SEVERITY_TONE[level]}
          />
        ))}
      </div>

      {visible.length === 0 ? (
        /* "Nothing matched the filter" and "the analysis raised nothing" are
           different statements, and only one of them is about the company. */
        model.risks.length === 0 ? (
          <EmptyState
            compact
            title="No findings were raised"
            description="Neither the deterministic checks nor the claim-level assessment flagged anything. That is an absence of red flags, not a clean bill of health — the Evidence view shows how much of the deck could be checked at all."
          />
        ) : (
          <EmptyState
            compact
            title="No findings at this severity"
            description="Change the filter to see the rest of the findings."
          />
        )
      ) : (
        <ul className="grid gap-3 xl:grid-cols-2">
          {visible.map((card) => (
            <RiskFinding
              key={card.risk.id}
              card={card}
              open={expanded.has(card.risk.id)}
              onToggle={() => toggle(card.risk.id)}
            />
          ))}
        </ul>
      )}
    </Section>
  );
}

function FilterPill({
  active,
  onClick,
  label,
  count,
  tone,
}: {
  active: boolean;
  onClick: () => void;
  label: string;
  count: number;
  tone?: Tone;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={cx(
        'inline-flex items-center gap-1.5 rounded-md border px-2.5 py-1 text-[13px] transition-colors',
        active
          ? 'border-line-strong bg-subtle font-medium text-fg'
          : 'border-transparent text-fg-2 hover:bg-subtle hover:text-fg',
      )}
    >
      {tone && <span aria-hidden className={cx('h-1.5 w-1.5 rounded-full', toneClasses.fill[tone])} />}
      {label}
      <span className="num text-2xs text-fg-3">{count}</span>
    </button>
  );
}

function RiskFinding({
  card,
  open,
  onToggle,
}: {
  card: RiskCardModel;
  open: boolean;
  onToggle: () => void;
}) {
  const { openClaim } = useReport();
  const { risk } = card;
  const tone = SEVERITY_TONE[card.severity];
  const state = card.state ? EVIDENCE_STATE[card.state] : null;
  const summary = firstSentence(risk.description);
  const hasMore =
    summary.trim() !== risk.description.trim() ||
    Boolean(risk.basis) ||
    card.questions.length > 0 ||
    risk.source_pages.length > 0;

  return (
    <Card
      as="li"
      className={cx('flex flex-col border-l-2 p-0', toneClasses.edge[tone])}
    >
      <div className="flex items-start gap-3 px-4 py-3.5">
        <div className="min-w-0 flex-1">
          <div className="mb-2 flex flex-wrap items-center gap-1.5">
            <SeverityBadge severity={card.severity} />
            <Badge>{humanise(risk.category)}</Badge>
            {state && (
              <Badge tone={state.tone} title={state.description}>
                {state.label}
              </Badge>
            )}
            {risk.is_rule_based && (
              <Badge title="Raised by a deterministic rule, not a model judgement.">
                Rule-based
              </Badge>
            )}
          </div>

          <h3 className="text-[14px] font-semibold leading-snug text-fg">{risk.title}</h3>
          <p className="mt-1.5 text-[13px] leading-relaxed text-fg-2">
            {open ? risk.description : summary}
          </p>
        </div>

        {hasMore && (
          <button
            type="button"
            onClick={onToggle}
            aria-expanded={open}
            aria-label={open ? 'Collapse finding' : 'Expand finding'}
            className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-md text-fg-3 transition-colors hover:bg-subtle hover:text-fg"
          >
            <svg
              width="11"
              height="11"
              viewBox="0 0 12 12"
              aria-hidden
              className={cx('transition-transform', open && 'rotate-180')}
            >
              <path
                d="M2 4.5L6 8.5L10 4.5"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.5"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
          </button>
        )}
      </div>

      {open && hasMore && (
        <div className="space-y-3 border-t border-line px-4 py-3.5">
          {risk.basis && (
            <Field label="What it rests on">
              <span className="text-fg-2">{risk.basis}</span>
            </Field>
          )}

          {state && (
            <Field label="Evidence state">
              <span className="text-fg-2">{state.description}</span>
            </Field>
          )}

          {card.questions.length > 0 && (
            <Field label="Recommended action">
              <ul className="space-y-1.5">
                {card.questions.map((question) => (
                  <li key={question.id} className="flex gap-2 text-fg-2">
                    <span aria-hidden className="mt-[7px] h-1 w-1 shrink-0 rounded-full bg-fg-3" />
                    <span>{question.question}</span>
                  </li>
                ))}
              </ul>
            </Field>
          )}

          {card.questions.length === 0 && card.claim?.claim.assessment?.key_uncertainties?.length ? (
            <Field label="Open uncertainties">
              <ul className="space-y-1.5">
                {card.claim.claim.assessment.key_uncertainties.slice(0, 3).map((item) => (
                  <li key={item} className="flex gap-2 text-fg-2">
                    <span aria-hidden className="mt-[7px] h-1 w-1 shrink-0 rounded-full bg-fg-3" />
                    <span>{item}</span>
                  </li>
                ))}
              </ul>
            </Field>
          ) : null}

          <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 pt-0.5 text-2xs text-fg-3">
            {risk.source_pages.length > 0 && (
              <span>
                Deck {risk.source_pages.length === 1 ? 'page' : 'pages'}{' '}
                <span className="num text-fg-2">{risk.source_pages.join(', ')}</span>
              </span>
            )}
            {card.claim && (
              <button
                type="button"
                className="underline decoration-line-strong underline-offset-2 hover:text-fg"
                onClick={() => openClaim(card.claim!.id)}
              >
                Open the source claim →
              </button>
            )}
          </div>
        </div>
      )}
    </Card>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="label mb-1">{label}</div>
      <div className="text-[13px] leading-relaxed">{children}</div>
    </div>
  );
}
