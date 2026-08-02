'use client';

import { formatDateTime, formatDuration } from '@/lib/format';
import { MetricCard } from '@/components/ui/primitives';
import { VIEWS } from '../navigation';
import { useReport } from '../context';

/**
 * Where to go next.
 *
 * The claim explorer, the evidence distribution, the management questions and
 * the appendix were all present before and all easy to miss: they were further
 * down a long scroll, announced only by a heading. Naming each one, saying what
 * it contains and how much of it there is turns "somewhere below" into a
 * decision the reader can make.
 */
export function ExploreSection() {
  const { model, goToSection } = useReport();

  const counts: Record<string, number> = {
    sections: model.sections.length,
    risks: model.risks.length,
    claims: model.claims.length,
    evidence: model.evidence.length,
    questions: model.questions.length,
  };

  const destinations = VIEWS.filter((view) => view.group !== 'answer');

  return (
    <section id="explore" aria-labelledby="explore-heading" className="scroll-anchor">
      <h2
        id="explore-heading"
        className="text-[17px] font-semibold tracking-[-0.01em] text-fg"
      >
        Go deeper
      </h2>
      <p className="mt-1 max-w-prose text-[13px] leading-relaxed text-fg-2">
        Everything behind the verdict. Each of these is the working for a part of it.
      </p>

      <div className="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
        {destinations.map((view) => {
          const count = view.countKey ? counts[view.countKey] : undefined;
          return (
            <button
              key={view.id}
              type="button"
              onClick={() => goToSection(view.id)}
              className="card group flex flex-col p-4 text-left transition-colors hover:border-line-strong hover:bg-subtle"
            >
              <div className="flex items-baseline justify-between gap-2">
                <span className="text-[13.5px] font-medium text-fg">{view.label}</span>
                {count !== undefined && count > 0 && (
                  <span className="num text-2xs text-fg-3">{count}</span>
                )}
              </div>
              <span className="mt-1.5 text-[13px] leading-relaxed text-fg-2">{view.blurb}</span>
              <span
                aria-hidden
                className="mt-3 text-[13px] text-fg-3 transition-colors group-hover:text-fg"
              >
                Open →
              </span>
            </button>
          );
        })}
      </div>
    </section>
  );
}

/**
 * The headline counters and the run's provenance, collapsed by default.
 *
 * These are diligence metadata: important when a reader is interrogating the
 * analysis, noise when they are reading the verdict for the first time. Native
 * `details` keeps keyboard and screen-reader behaviour correct for free, and
 * the state is remembered by the browser across a reload.
 */
export function AtAGlanceSection() {
  const { model, goToSection } = useReport();

  return (
    <details id="at-a-glance" className="card group scroll-anchor px-5 py-4 open:pb-5">
      <summary className="flex cursor-pointer list-none items-center justify-between gap-3 rounded-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-fg/30">
        <div className="min-w-0">
          <h2 className="text-[14px] font-semibold text-fg">Analysis at a glance</h2>
          <p className="mt-0.5 text-[13px] text-fg-2">
            Claim counts, verification coverage and how this run was produced.
          </p>
        </div>
        <svg
          aria-hidden
          width="14"
          height="14"
          viewBox="0 0 16 16"
          className="shrink-0 text-fg-3 transition-transform group-open:rotate-90"
        >
          <path
            d="M6 3.5L10.5 8 6 12.5"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.5"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      </summary>

      <div className="mt-5 grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6">
        {model.stats.map((stat) => (
          <MetricCard
            key={stat.key}
            label={stat.label}
            value={stat.value}
            hint={stat.hint}
            tone={stat.tone}
            onClick={stat.target ? () => goToSection(stat.target!) : undefined}
          />
        ))}
      </div>

      <dl className="mt-4 flex flex-wrap items-center gap-x-6 gap-y-2 rounded-card border border-line bg-subtle/60 px-4 py-3 text-[12.5px]">
        <MetaItem label="Report generated" value={formatDateTime(model.createdAt)} />
        <MetaItem label="Analysis duration" value={formatDuration(model.durationMs)} />
        <MetaItem label="Source" value={`${model.filename} · ${model.pageCount} pages`} />
        <MetaItem label="Pipeline" value={`v${model.pipelineVersion}`} />
      </dl>
    </details>
  );
}

function MetaItem({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex min-w-0 items-baseline gap-2">
      <dt className="label shrink-0">{label}</dt>
      <dd className="num truncate text-fg-2">{value}</dd>
    </div>
  );
}
