import Link from 'next/link';

import { UploadPanel } from '@/components/UploadPanel';
import { api, ApiRequestError } from '@/lib/api';
import { Callout, Chip, EmptyState, PageHeader, formatBytes, formatDate } from '@/components/ui';
import type { DocumentSummary, Health } from '@/lib/types';

export const dynamic = 'force-dynamic';

export default async function HomePage() {
  let documents: DocumentSummary[] = [];
  let health: Health | null = null;
  let apiDown = false;

  try {
    [health, documents] = await Promise.all([
      api.health(),
      api.listDocuments(20).then((page) => page.items),
    ]);
  } catch (error) {
    apiDown = !(error instanceof ApiRequestError) || error.status >= 500;
  }

  return (
    <div className="space-y-8">
      <PageHeader
        title="Scientific due diligence"
        subtitle="Upload a biotech pitch deck. BioIntel extracts every scientific claim, checks it against published literature and trial registries, and drafts an Investment Committee memo."
      />

      {apiDown && (
        <Callout tone="danger" title="The BioIntel API is not reachable">
          Start the backend with <code className="font-mono">uvicorn app.main:app --reload</code>{' '}
          from the <code className="font-mono">backend</code> directory, then reload this page.
        </Callout>
      )}

      {health?.llm_degraded && (
        <Callout tone="warning" title="Running without a language-model provider">
          No <code className="font-mono">OPENAI_API_KEY</code> is configured, so analyses use
          BioIntel&apos;s deterministic offline analyser. Claims and evidence are still extracted and
          linked, but figures are not interpreted and evidence is matched lexically rather than
          semantically. Reports are marked as degraded.
        </Callout>
      )}

      <div className="grid gap-6 lg:grid-cols-[1.4fr_1fr]">
        <UploadPanel />

        <div className="card">
          <h2 className="text-base font-semibold">What you get</h2>
          <ol className="mt-3 space-y-3 text-sm text-ink-600 dark:text-ink-400">
            {[
              ['Claims with provenance', 'Every extracted claim carries the verbatim quote and page it came from, verified against the document.'],
              ['Literature adjudication', 'Each claim is searched against PubMed, Europe PMC and ClinicalTrials.gov, and each record is judged as supporting, contradicting or unrelated.'],
              ['Credibility scoring', 'A deterministic, fully broken-down score combines the evidence tier the deck offers with what the literature actually shows.'],
              ['Diligence questions', 'The specific technical questions to put to the company, with what a good answer looks like.'],
              ['IC memo', 'A cited, exportable first-draft memo separating what the company claims, what the evidence shows, and what BioIntel infers.'],
            ].map(([title, body], index) => (
              <li key={title} className="flex gap-3">
                <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-ink-200 text-[11px] font-semibold text-ink-700 dark:bg-ink-800 dark:text-ink-300">
                  {index + 1}
                </span>
                <span>
                  <span className="font-medium text-ink-900 dark:text-ink-100">{title}.</span>{' '}
                  {body}
                </span>
              </li>
            ))}
          </ol>
        </div>
      </div>

      <section>
        <h2 className="mb-3 text-base font-semibold">Recent decks</h2>
        {documents.length === 0 ? (
          <EmptyState
            title="No decks yet"
            description="Upload a pitch deck above to run your first analysis."
          />
        ) : (
          <div className="overflow-hidden rounded-lg border border-ink-200 dark:border-ink-800">
            <table className="w-full text-sm">
              <thead className="bg-ink-100 text-left dark:bg-ink-900">
                <tr>
                  <th className="px-4 py-2.5 font-medium">Document</th>
                  <th className="px-4 py-2.5 font-medium">Pages</th>
                  <th className="px-4 py-2.5 font-medium">Size</th>
                  <th className="px-4 py-2.5 font-medium">Uploaded</th>
                  <th className="px-4 py-2.5" />
                </tr>
              </thead>
              <tbody className="divide-y divide-ink-200 bg-white dark:divide-ink-800 dark:bg-ink-950">
                {documents.map((document) => (
                  <tr key={document.id} className="hover:bg-ink-50 dark:hover:bg-ink-900">
                    <td className="px-4 py-3">
                      <Link
                        href={`/documents/${document.id}`}
                        className="font-medium hover:underline"
                      >
                        {document.filename}
                      </Link>
                      {document.requires_ocr && (
                        <span className="ml-2">
                          <Chip>scanned</Chip>
                        </span>
                      )}
                    </td>
                    <td className="px-4 py-3 tabular-nums text-ink-600 dark:text-ink-400">
                      {document.page_count}
                    </td>
                    <td className="px-4 py-3 tabular-nums text-ink-600 dark:text-ink-400">
                      {formatBytes(document.size_bytes)}
                    </td>
                    <td className="px-4 py-3 text-ink-600 dark:text-ink-400">
                      {formatDate(document.created_at)}
                    </td>
                    <td className="px-4 py-3 text-right">
                      <Link
                        href={`/documents/${document.id}`}
                        className="text-ink-500 hover:text-ink-900 dark:hover:text-ink-100"
                      >
                        Open →
                      </Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
