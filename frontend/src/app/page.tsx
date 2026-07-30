import Link from 'next/link';

import { UploadPanel } from '@/components/UploadPanel';
import { api, ApiRequestError } from '@/lib/api';
import { Badge, Callout, Card, EmptyState, PageHeader } from '@/components/ui';
import { formatBytes, formatDate } from '@/lib/format';
import type { DocumentSummary, Health } from '@/lib/types';

export const dynamic = 'force-dynamic';

const CAPABILITIES: Array<[string, string]> = [
  [
    'Claims with provenance',
    'Every extracted claim carries the verbatim quote and page it came from, verified against the document.',
  ],
  [
    'Literature adjudication',
    'Each claim is searched against PubMed, Europe PMC and ClinicalTrials.gov, and each record is judged as supporting, contradicting or unrelated.',
  ],
  [
    'Credibility scoring',
    'A deterministic, fully broken-down score combines the evidence tier the deck offers with what the literature actually shows.',
  ],
  [
    'Diligence questions',
    'The specific technical questions to put to the company, with what a good answer looks like.',
  ],
  [
    'IC memo',
    'A cited, exportable first-draft memo separating what the company claims, what the evidence shows, and what BioIntel infers.',
  ],
];

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
    <div className="page space-y-8">
      <PageHeader
        title="Scientific due diligence"
        subtitle="Upload a biotech pitch deck. BioIntel extracts every scientific claim, checks it against published literature and trial registries, and drafts an Investment Committee memo."
      />

      {apiDown && (
        <Callout tone="crit" title="The BioIntel API is not reachable">
          Start the backend with{' '}
          <code className="rounded border border-line bg-subtle px-1 font-mono text-2xs">
            uvicorn app.main:app --reload
          </code>{' '}
          from the <code className="font-mono">backend</code> directory, then reload this page.
        </Callout>
      )}

      {health?.llm_degraded && (
        <Callout tone="warn" title="Running without a language-model provider">
          No <code className="font-mono">OPENAI_API_KEY</code> is configured, so analyses use
          BioIntel&apos;s deterministic offline analyser. Claims and evidence are still extracted and
          linked, but figures are not interpreted and evidence is matched lexically rather than
          semantically. Reports are marked as degraded.
        </Callout>
      )}

      <div className="grid gap-5 lg:grid-cols-[1.4fr_1fr]">
        <UploadPanel />

        <Card className="p-6">
          <h2 className="text-[15px] font-semibold tracking-[-0.01em]">What you get</h2>
          <ol className="mt-4 space-y-3.5">
            {CAPABILITIES.map(([title, body], index) => (
              <li key={title} className="flex gap-3">
                <span className="num mt-px flex h-[18px] w-[18px] shrink-0 items-center justify-center rounded-full bg-subtle text-2xs font-semibold text-fg-2">
                  {index + 1}
                </span>
                <span className="text-[13px] leading-relaxed text-fg-2">
                  <span className="font-medium text-fg">{title}.</span> {body}
                </span>
              </li>
            ))}
          </ol>
        </Card>
      </div>

      <section>
        <h2 className="mb-3 text-[15px] font-semibold tracking-[-0.01em]">Recent decks</h2>
        {documents.length === 0 ? (
          <EmptyState
            title="No decks yet"
            description="Upload a pitch deck above to run your first analysis."
          />
        ) : (
          <Card className="overflow-hidden">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Document</th>
                  <th className="w-[80px]">Pages</th>
                  <th className="w-[100px]">Size</th>
                  <th className="w-[150px]">Uploaded</th>
                  <th className="w-[90px]" />
                </tr>
              </thead>
              <tbody>
                {documents.map((document) => (
                  <tr key={document.id}>
                    <td>
                      <Link
                        href={`/documents/${document.id}`}
                        className="font-medium text-fg hover:underline"
                      >
                        {document.filename}
                      </Link>
                      {document.requires_ocr && (
                        <span className="ml-2 align-middle">
                          <Badge>Scanned</Badge>
                        </span>
                      )}
                    </td>
                    <td className="num text-fg-2">{document.page_count}</td>
                    <td className="num text-fg-2">{formatBytes(document.size_bytes)}</td>
                    <td className="text-fg-2">{formatDate(document.created_at)}</td>
                    <td className="text-right">
                      <Link
                        href={`/documents/${document.id}`}
                        className="text-fg-3 transition-colors hover:text-fg"
                      >
                        Open →
                      </Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>
        )}
      </section>
    </div>
  );
}
