'use client';

import { cleanQuote, humanise } from '@/lib/format';
import {
  EVIDENCE_STATE,
  SCORE_EFFECT_META,
  SEVERITY_TONE,
  sourceLabel,
  type ClaimRow,
} from '@/lib/report-model';
import type { EvidenceLink } from '@/lib/types';
import { Drawer, DrawerSection } from '@/components/ui/Drawer';
import {
  Badge,
  BandBadge,
  Meter,
  PriorityBadge,
  StanceBadge,
  cx,
  toneClasses,
} from '@/components/ui/primitives';

/**
 * Everything known about one claim, in the order an analyst asks for it.
 *
 * This is the bottom of the traceability chain — recommendation, finding,
 * claim, evidence, source — so it must never dead-end: the verbatim quote
 * carries its page, and every evidence record carries its external identifier
 * and a link out.
 */
export function ClaimDrawer({
  row,
  open,
  onClose,
}: {
  row: ClaimRow | null;
  open: boolean;
  onClose: () => void;
}) {
  if (!row) return null;

  const assessment = row.claim.assessment;
  const state = EVIDENCE_STATE[row.state];
  const effect = SCORE_EFFECT_META[row.effect];
  const sources = [...new Set(row.evidenceLinks.map((link) => link.evidence.source))];

  return (
    <Drawer
      open={open}
      onClose={onClose}
      eyebrow={
        <>
          <Badge tone={state.tone} title={state.description}>
            {state.label}
          </Badge>
          <Badge>{row.typeLabel}</Badge>
          <Badge>Deck p.{row.page}</Badge>
          {row.claim.is_thesis_critical && <Badge tone="info">Thesis-critical</Badge>}
        </>
      }
      title={row.statement}
    >
      {/* ------------------------------------------------------- source --- */}
      <DrawerSection title="Verbatim source">
        <blockquote className="rounded-lg border border-line border-l-2 border-l-line-strong bg-subtle px-3.5 py-3 text-[13px] italic leading-relaxed text-fg-2">
          “{cleanQuote(row.quote)}”
        </blockquote>
        <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-2xs text-fg-3">
          <span>
            Page <span className="num text-fg-2">{row.page}</span>
          </span>
          <span>
            Quote match{' '}
            <span className="num text-fg-2">{row.claim.quote_match_score.toFixed(2)}</span> ·{' '}
            {humanise(row.claim.quote_verification)}
          </span>
          {row.claim.from_visual && <span>Read from a figure</span>}
          {row.claim.hedging_language && <span>Hedged phrasing</span>}
        </div>
      </DrawerSection>

      {/* -------------------------------------------------- score/state --- */}
      <DrawerSection title="Assessment">
        <div className="grid grid-cols-2 gap-3">
          <ScoreTile
            label="Credibility"
            value={row.score === null ? '—' : row.score.toFixed(0)}
            sub={
              row.band ? (
                <BandBadge band={row.band} />
              ) : (
                <span className="text-2xs text-fg-3">Excluded from scoring</span>
              )
            }
            meter={row.score ?? 0}
          />
          <ScoreTile
            label="Confidence"
            value={row.confidence === null ? '—' : `${(row.confidence * 100).toFixed(0)}%`}
            sub={
              <span className="text-2xs text-fg-3">
                {row.confidence === null
                  ? 'Not assessed'
                  : row.confidence >= 0.7
                    ? 'Well supported'
                    : row.confidence >= 0.45
                      ? 'Partially supported'
                      : 'Thinly supported'}
              </span>
            }
            meter={(row.confidence ?? 0) * 100}
          />
        </div>

        <div className="mt-3 flex flex-wrap items-center gap-1.5">
          <Badge tone={effect.tone}>
            {effect.label}
            {row.effectDelta !== null && row.effectDelta !== 0 && (
              <span className="num">
                {row.effectDelta > 0 ? '+' : ''}
                {row.effectDelta.toFixed(0)}
              </span>
            )}
          </Badge>
          <span className="text-2xs text-fg-3">
            relative to the no-information anchor, weighted by importance{' '}
            <span className="num">{Math.round(row.importance * 100)}</span>
          </span>
        </div>

        {assessment?.score_explanation && (
          <p className="mt-3 text-[13px] leading-relaxed text-fg-2">
            {assessment.score_explanation}
          </p>
        )}
      </DrawerSection>

      {/* ----------------------------------------------------- reasoning --- */}
      {assessment && (
        <DrawerSection title="Reasoning">
          {assessment.verdict && (
            <p className="text-[13px] leading-relaxed text-fg">{assessment.verdict}</p>
          )}
          {assessment.corroboration_rationale && (
            <p className="mt-2.5 text-[13px] leading-relaxed text-fg-2">
              {assessment.corroboration_rationale}
            </p>
          )}
          <p className="mt-3 rounded-lg border border-line bg-subtle px-3 py-2 text-2xs leading-relaxed text-fg-2">
            <span className="font-semibold text-fg">{state.label}.</span> {state.description}
          </p>
        </DrawerSection>
      )}

      {/* --------------------------------------------------- verification --- */}
      {assessment?.verification_detail && (
        <DrawerSection
          title="Registry verification"
          aside={
            assessment.verification_source ? (
              <Badge>{sourceLabel(assessment.verification_source)}</Badge>
            ) : null
          }
        >
          <p className="text-[13px] leading-relaxed text-fg-2">
            {assessment.verification_detail}
          </p>
          {assessment.verification_identifiers.length > 0 && (
            <div className="mt-2 flex flex-wrap gap-1.5">
              {assessment.verification_identifiers.map((identifier) => (
                <Badge key={identifier}>
                  <span className="num font-mono">{identifier}</span>
                </Badge>
              ))}
            </div>
          )}
        </DrawerSection>
      )}

      {/* -------------------------------------------------------- evidence --- */}
      <DrawerSection
        title={`Evidence found · ${row.evidenceLinks.length}`}
        aside={
          row.evidenceLinks.length > 0 ? (
            <span className="text-2xs text-fg-3">
              <span className="text-pos">{row.supporting} for</span> ·{' '}
              <span className="text-crit">{row.contradicting} against</span> · {row.neutral} neutral
            </span>
          ) : null
        }
      >
        {sources.length > 0 && (
          <div className="mb-3 flex flex-wrap gap-1.5">
            {sources.map((source) => (
              <Badge key={source}>{sourceLabel(source)}</Badge>
            ))}
          </div>
        )}

        {row.evidenceLinks.length === 0 ? (
          <p className="text-[13px] leading-relaxed text-fg-3">
            No external record was matched to this claim. {state.description}
          </p>
        ) : (
          <ul className="space-y-3">
            {row.evidenceLinks.map((link) => (
              <EvidenceRecord key={link.id} link={link} />
            ))}
          </ul>
        )}
      </DrawerSection>

      {/* -------------------------------------------------- uncertainties --- */}
      {assessment && assessment.key_uncertainties.length > 0 && (
        <DrawerSection title="Open uncertainties">
          <ul className="space-y-1.5">
            {assessment.key_uncertainties.map((item) => (
              <li key={item} className="flex gap-2 text-[13px] leading-relaxed text-fg-2">
                <span aria-hidden className="mt-[7px] h-1 w-1 shrink-0 rounded-full bg-fg-3" />
                <span>{item}</span>
              </li>
            ))}
          </ul>
        </DrawerSection>
      )}

      {/* ----------------------------------------------------- diligence --- */}
      {(row.questions.length > 0 || row.risks.length > 0) && (
        <DrawerSection title="Recommended diligence action">
          {row.risks.length > 0 && (
            <ul className="mb-3 space-y-1.5">
              {row.risks.map((risk) => (
                <li key={risk.id} className="flex items-start gap-2">
                  <Badge tone={SEVERITY_TONE[risk.severity]}>{humanise(risk.severity)}</Badge>
                  <span className="text-[13px] leading-snug text-fg-2">{risk.title}</span>
                </li>
              ))}
            </ul>
          )}
          {row.questions.length === 0 ? (
            <p className="text-[13px] text-fg-3">
              No management question was generated for this claim.
            </p>
          ) : (
            <ul className="space-y-3">
              {row.questions.map((question) => (
                <li key={question.id} className="rounded-lg border border-line bg-subtle p-3">
                  <div className="mb-1.5 flex flex-wrap items-center gap-1.5">
                    <PriorityBadge priority={question.priority} />
                    <Badge>{humanise(question.category)}</Badge>
                  </div>
                  <p className="text-[13px] font-medium leading-snug text-fg">
                    {question.question}
                  </p>
                  {question.what_good_looks_like && (
                    <p className="mt-1.5 text-2xs leading-relaxed text-fg-2">
                      <span className="label">A good answer </span>
                      {question.what_good_looks_like}
                    </p>
                  )}
                </li>
              ))}
            </ul>
          )}
        </DrawerSection>
      )}

      {/* ---------------------------------------------------------- meta --- */}
      <DrawerSection title="Extraction metadata">
        <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-[13px]">
          <Meta label="Category" value={row.categoryLabel} />
          <Meta label="Claimed evidence tier" value={humanise(row.claim.claimed_evidence_tier)} />
          <Meta
            label="Extraction confidence"
            value={row.claim.extraction_confidence.toFixed(2)}
          />
          <Meta label="Falsifiable" value={row.claim.is_falsifiable ? 'Yes' : 'No'} />
        </dl>
        {row.claim.entities.length > 0 && (
          <div className="mt-3 flex flex-wrap gap-1.5">
            {row.claim.entities.map((entity) => (
              <Badge key={entity.id} title={humanise(entity.entity_type)}>
                {entity.canonical_name ?? entity.name}
              </Badge>
            ))}
          </div>
        )}
        {row.claim.review_reasons.length > 0 && (
          <ul className="mt-3 space-y-1">
            {row.claim.review_reasons.map((reason) => (
              <li key={reason} className="text-2xs leading-relaxed text-warn">
                ⚠ {reason}
              </li>
            ))}
          </ul>
        )}
      </DrawerSection>
    </Drawer>
  );
}

function ScoreTile({
  label,
  value,
  sub,
  meter,
}: {
  label: string;
  value: string;
  sub: React.ReactNode;
  meter: number;
}) {
  return (
    <div className="rounded-lg border border-line p-3">
      <div className="label">{label}</div>
      <div className="num mt-1 text-2xl font-semibold leading-none text-fg">{value}</div>
      <Meter className="mt-2" value={meter} tone="neutral" label={label} />
      <div className="mt-2">{sub}</div>
    </div>
  );
}

function Meta({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="label">{label}</dt>
      <dd className="mt-0.5 text-fg-2">{value}</dd>
    </div>
  );
}

function EvidenceRecord({ link }: { link: EvidenceLink }) {
  const record = link.evidence;
  const identifiers = [
    record.pmid && `PMID ${record.pmid}`,
    record.nct_id,
    record.doi && `DOI ${record.doi}`,
  ].filter(Boolean) as string[];

  return (
    <li className="rounded-lg border border-line p-3">
      <div className="mb-2 flex flex-wrap items-center gap-1.5">
        <StanceBadge stance={link.stance} />
        <Badge>{sourceLabel(record.source)}</Badge>
        {record.study_design && <Badge>{humanise(record.study_design)}</Badge>}
        {record.is_retracted && <Badge tone="crit">Retracted</Badge>}
        {record.is_preprint && <Badge tone="warn">Preprint</Badge>}
        <span className="num ml-auto text-2xs text-fg-3">
          rel {link.relevance.toFixed(2)} · str {link.strength.toFixed(2)}
        </span>
      </div>

      {record.url ? (
        <a
          href={record.url}
          target="_blank"
          rel="noreferrer noopener"
          className="text-[13px] font-medium leading-snug text-fg hover:underline"
        >
          {record.title}
        </a>
      ) : (
        <span className="text-[13px] font-medium leading-snug text-fg">{record.title}</span>
      )}

      <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-2xs text-fg-3">
        {[record.journal, record.publication_year].filter(Boolean).map((part) => (
          <span key={String(part)}>{part}</span>
        ))}
        {identifiers.map((identifier) => (
          <span key={identifier} className="num font-mono">
            {identifier}
          </span>
        ))}
      </div>

      {link.supporting_quote && (
        <blockquote
          className={cx(
            'mt-2 border-l-2 pl-2.5 text-2xs italic leading-relaxed text-fg-2',
            toneClasses.edge[link.stance === 'contradicts' ? 'crit' : 'neutral'],
          )}
        >
          “{cleanQuote(link.supporting_quote)}”
        </blockquote>
      )}

      {link.rationale && (
        <p className="mt-2 text-2xs leading-relaxed text-fg-2">{link.rationale}</p>
      )}

      {link.caveats.length > 0 && (
        <p className="mt-1.5 text-2xs leading-relaxed text-fg-3">
          Caveats: {link.caveats.join('; ')}
        </p>
      )}
    </li>
  );
}
