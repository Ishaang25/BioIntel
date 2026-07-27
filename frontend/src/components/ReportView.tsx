import { markdownToHtml } from '@/lib/markdown';
import type { Report } from '@/lib/types';

/**
 * The Investment Committee memo.
 *
 * The memo markdown is rendered on the server by our own small converter,
 * which escapes all HTML before emitting any markup — model output is
 * untrusted input and must never reach the DOM as raw HTML.
 */
export function ReportView({ report }: { report: Report }) {
  return (
    <article className="card">
      <h2 className="text-xl font-semibold tracking-tight">{report.title}</h2>

      <section className="mt-5">
        <h3 className="label">Executive summary</h3>
        <p className="mt-2 whitespace-pre-line text-[15px] leading-relaxed">
          {report.executive_summary}
        </p>
      </section>

      {report.sections.map((section) => (
        <section key={section.id} className="mt-8">
          <h3 className="mb-2 border-b border-ink-200 pb-1 text-lg font-semibold tracking-tight dark:border-ink-800">
            {section.heading}
          </h3>
          <div
            className="prose-report"
            dangerouslySetInnerHTML={{ __html: markdownToHtml(section.body_markdown) }}
          />
        </section>
      ))}

      <section className="mt-8">
        <h3 className="mb-2 border-b border-ink-200 pb-1 text-lg font-semibold tracking-tight dark:border-ink-800">
          Recommendation
        </h3>
        <div
          className="prose-report"
          dangerouslySetInnerHTML={{ __html: markdownToHtml(report.recommendation) }}
        />
      </section>

      {report.citations.length > 0 && (
        <section className="mt-8">
          <h3 className="mb-2 border-b border-ink-200 pb-1 text-lg font-semibold tracking-tight dark:border-ink-800">
            References
          </h3>
          <ol className="space-y-1.5 text-sm">
            {report.citations.map((citation) => {
              const ref = String(citation.ref ?? '');
              const url = typeof citation.url === 'string' ? citation.url : null;
              const title =
                (typeof citation.title === 'string' && citation.title) ||
                (typeof citation.statement === 'string' && citation.statement) ||
                'Untitled';
              const meta = [citation.journal, citation.year, citation.citation_label]
                .filter(Boolean)
                .join(' · ');
              return (
                <li key={ref} className="flex gap-2">
                  <span className="font-mono text-xs text-ink-400">[{ref}]</span>
                  <span>
                    {url ? (
                      <a href={url} target="_blank" rel="noreferrer noopener" className="hover:underline">
                        {title}
                      </a>
                    ) : (
                      title
                    )}
                    {meta && <span className="ml-1 text-xs text-ink-500">{meta}</span>}
                  </span>
                </li>
              );
            })}
          </ol>
        </section>
      )}

      {report.limitations.length > 0 && (
        <section className="mt-8 rounded-md border border-amber-500/30 bg-amber-500/5 p-4">
          <h3 className="text-sm font-semibold">Limitations of this analysis</h3>
          <ul className="mt-2 space-y-1 text-sm text-ink-600 dark:text-ink-400">
            {report.limitations.map((limitation) => (
              <li key={limitation}>• {limitation}</li>
            ))}
          </ul>
        </section>
      )}
    </article>
  );
}
