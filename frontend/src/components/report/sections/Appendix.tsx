'use client';

import { useMemo, useState } from 'react';

import { formatDateTime, formatDuration, humanise } from '@/lib/format';
import type { Entity } from '@/lib/types';
import { Badge, Card, cx } from '@/components/ui/primitives';
import { useReport } from '../context';
import { Section } from './Section';

type Tab = 'references' | 'entities' | 'assessment' | 'limitations' | 'provenance';

const TABS: Array<{ id: Tab; label: string }> = [
  { id: 'references', label: 'References' },
  { id: 'entities', label: 'Entities' },
  { id: 'assessment', label: 'Scientific assessment' },
  { id: 'limitations', label: 'Limitations' },
  { id: 'provenance', label: 'Provenance' },
];

/**
 * Everything a reader may need to audit the report but should not have to
 * scroll past to read it: the reference list, the extracted entity map, the
 * qualitative assessment, the stated limitations and the run's own provenance.
 */
export function AppendixSection() {
  const { model, openClaim } = useReport();
  const [tab, setTab] = useState<Tab>('references');

  const available = useMemo(
    () =>
      TABS.filter((entry) => {
        if (entry.id === 'references') return model.citations.length > 0;
        if (entry.id === 'entities') return model.entities.length > 0;
        if (entry.id === 'assessment') return model.scientificAssessment.length > 0;
        if (entry.id === 'limitations') return model.limitations.length > 0;
        return true;
      }),
    [model],
  );

  const active = available.some((entry) => entry.id === tab) ? tab : (available[0]?.id ?? 'provenance');

  return (
    <Section
      id="appendix"
      title="Appendix"
      description="Reference list, extracted entities, qualitative assessment, stated limitations and the provenance of this run."
    >
      <Card className="overflow-hidden">
        <div role="tablist" aria-label="Appendix" className="flex gap-1 border-b border-line p-2">
          {available.map((entry) => (
            <button
              key={entry.id}
              role="tab"
              type="button"
              aria-selected={active === entry.id}
              onClick={() => setTab(entry.id)}
              className={cx(
                'rounded-md px-2.5 py-1.5 text-[13px] transition-colors',
                active === entry.id
                  ? 'bg-subtle font-medium text-fg'
                  : 'text-fg-2 hover:bg-subtle/70 hover:text-fg',
              )}
            >
              {entry.label}
            </button>
          ))}
        </div>

        <div className="p-5">
          {active === 'references' && (
            <ol className="space-y-2.5">
              {model.citations.map((citation) => (
                <li key={citation.ref} className="flex gap-3 text-[13px]">
                  <span className="num shrink-0 self-start rounded border border-line bg-subtle px-1.5 py-px font-mono text-2xs leading-5 text-fg-2">
                    {citation.ref}
                  </span>
                  <div className="min-w-0 flex-1">
                    <p className="leading-snug text-fg">{citation.title}</p>
                    <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-2xs text-fg-3">
                      {citation.pageNumber !== null && (
                        <span className="num">Deck p.{citation.pageNumber}</span>
                      )}
                      {citation.journal && <span>{citation.journal}</span>}
                      {citation.year !== null && <span className="num">{citation.year}</span>}
                      {citation.pmid && <span className="num font-mono">PMID {citation.pmid}</span>}
                      {citation.url && (
                        <a
                          href={citation.url}
                          target="_blank"
                          rel="noreferrer noopener"
                          className="underline decoration-line-strong underline-offset-2 hover:text-fg"
                        >
                          source
                        </a>
                      )}
                      {citation.claimId && model.claimsById.has(citation.claimId) && (
                        <button
                          type="button"
                          onClick={() => openClaim(citation.claimId!)}
                          className="underline decoration-line-strong underline-offset-2 hover:text-fg"
                        >
                          open claim →
                        </button>
                      )}
                    </div>
                  </div>
                </li>
              ))}
            </ol>
          )}

          {active === 'entities' && <EntityGrid entities={model.entities} />}

          {active === 'assessment' && (
            <dl className="grid gap-4 md:grid-cols-2">
              {model.scientificAssessment.map((entry) => (
                <div key={entry.key}>
                  <dt className="label">{entry.label}</dt>
                  <dd className="mt-1 text-[13px] leading-relaxed text-fg-2">{entry.value}</dd>
                </div>
              ))}
            </dl>
          )}

          {active === 'limitations' && (
            <ul className="space-y-2.5">
              {model.limitations.map((limitation) => (
                <li key={limitation} className="flex gap-2.5 text-[13px] leading-relaxed">
                  <span aria-hidden className="mt-[7px] h-1 w-1 shrink-0 rounded-full bg-warn" />
                  <span className="text-fg-2">{limitation}</span>
                </li>
              ))}
            </ul>
          )}

          {active === 'provenance' && <Provenance />}
        </div>
      </Card>
    </Section>
  );
}

function EntityGrid({ entities }: { entities: Entity[] }) {
  const grouped = useMemo(() => {
    const buckets = new Map<string, Entity[]>();
    for (const entity of entities) {
      const bucket = buckets.get(entity.entity_type);
      if (bucket) bucket.push(entity);
      else buckets.set(entity.entity_type, [entity]);
    }
    return [...buckets.entries()].sort((a, b) => b[1].length - a[1].length);
  }, [entities]);

  return (
    <div className="grid gap-5 md:grid-cols-2 xl:grid-cols-3">
      {grouped.map(([type, group]) => (
        <div key={type}>
          <h4 className="label mb-2">
            {humanise(type)} · {group.length}
          </h4>
          <ul className="space-y-1.5">
            {group
              .slice()
              .sort((a, b) => b.salience - a.salience)
              .map((entity) => (
                <li key={entity.id} className="text-[13px]">
                  <div className="flex items-baseline justify-between gap-2">
                    <span className="truncate font-medium text-fg">
                      {entity.canonical_name ?? entity.name}
                    </span>
                    <span className="num shrink-0 text-2xs text-fg-3">
                      {entity.salience.toFixed(2)}
                    </span>
                  </div>
                  {entity.description && (
                    <p className="mt-0.5 text-2xs leading-relaxed text-fg-3">
                      {entity.description}
                    </p>
                  )}
                </li>
              ))}
          </ul>
        </div>
      ))}
    </div>
  );
}

function Provenance() {
  const { model } = useReport();
  return (
    <div className="grid gap-5 md:grid-cols-2">
      <dl className="space-y-2.5 text-[13px]">
        <Row label="Run" value={model.runId} mono />
        <Row label="Document" value={model.filename} />
        <Row label="Pages" value={String(model.pageCount)} />
        <Row label="Pipeline version" value={model.pipelineVersion} />
        <Row label="Report generated" value={formatDateTime(model.createdAt)} />
        <Row label="Analysis duration" value={formatDuration(model.durationMs)} />
      </dl>
      <div>
        <h4 className="label mb-2">Traceability</h4>
        <ol className="space-y-1.5 text-[13px] text-fg-2">
          {[
            'Recommendation — derived from the scorecard, not written freehand.',
            'Finding — every risk names the claim or rule that produced it.',
            'Claim — every claim carries its verbatim quote and page number.',
            'Evidence — every adjudication states its stance and rationale.',
            'Source — every record links to PubMed, Europe PMC or the registry.',
          ].map((step, index) => (
            <li key={step} className="flex gap-2.5">
              <span className="num mt-px shrink-0 text-2xs text-fg-3">{index + 1}</span>
              <span>{step}</span>
            </li>
          ))}
        </ol>
        {model.degraded && (
          <Badge tone="warn" className="mt-3">
            Produced without a language-model provider
          </Badge>
        )}
      </div>
    </div>
  );
}

function Row({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="flex items-baseline justify-between gap-4">
      <dt className="label shrink-0">{label}</dt>
      <dd className={cx('min-w-0 truncate text-right text-fg-2', mono && 'num font-mono text-2xs')}>
        {value}
      </dd>
    </div>
  );
}
