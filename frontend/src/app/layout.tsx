import type { Metadata } from 'next';
import Link from 'next/link';

import './globals.css';

export const metadata: Metadata = {
  title: 'BioIntel — Scientific due diligence for biotech investing',
  description:
    'Upload a biotech pitch deck and receive an evidence-linked scientific due-diligence memo: extracted claims with page-level provenance, literature adjudication, credibility scoring and diligence questions.',
  robots: { index: false, follow: false },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen">
        <div className="flex min-h-screen flex-col">
          <nav className="border-b border-ink-200 bg-white/80 backdrop-blur dark:border-ink-800 dark:bg-ink-950/80">
            <div className="mx-auto flex max-w-6xl items-center justify-between px-6 py-3">
              <Link href="/" className="flex items-center gap-2.5">
                <span
                  aria-hidden
                  className="flex h-7 w-7 items-center justify-center rounded bg-ink-900 text-xs font-bold text-white dark:bg-ink-100 dark:text-ink-900"
                >
                  Bi
                </span>
                <span className="text-[15px] font-semibold tracking-tight">BioIntel</span>
              </Link>
              <div className="flex items-center gap-5 text-sm text-ink-600 dark:text-ink-400">
                <Link href="/" className="hover:text-ink-900 dark:hover:text-ink-100">
                  Decks
                </Link>
                <Link href="/runs" className="hover:text-ink-900 dark:hover:text-ink-100">
                  Analyses
                </Link>
              </div>
            </div>
          </nav>

          <main className="mx-auto w-full max-w-6xl flex-1 px-6 py-8">{children}</main>

          <footer className="border-t border-ink-200 py-6 dark:border-ink-800">
            <div className="mx-auto max-w-6xl px-6 text-xs text-ink-500">
              BioIntel produces first-draft scientific analysis for investment diligence. Every
              claim is traceable to a page of the source document and every citation to a retrieved
              record. Outputs require review by a qualified scientific advisor before informing an
              investment decision.
            </div>
          </footer>
        </div>
      </body>
    </html>
  );
}
