import type { Metadata, Viewport } from 'next';
import Link from 'next/link';

import './globals.css';

export const metadata: Metadata = {
  title: 'BioIntel — Scientific due diligence for biotech investing',
  description:
    'Upload a biotech pitch deck and receive an evidence-linked scientific due-diligence memo: extracted claims with page-level provenance, literature adjudication, credibility scoring and diligence questions.',
  robots: { index: false, follow: false },
};

export const viewport: Viewport = {
  // The workspace is built for 1024px and up; let tablets render it at scale
  // rather than reflowing into a phone layout.
  width: 'device-width',
  initialScale: 1,
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen">
        {/* The report's tab bar sits between the header and the content, so a
            keyboard reader would otherwise cross it on every page. */}
        <a
          href="#content"
          className="sr-only focus:not-sr-only focus:absolute focus:left-3 focus:top-3 focus:z-50 focus:rounded-md focus:bg-panel focus:px-3 focus:py-2 focus:text-[13px] focus:text-fg focus:shadow-lg focus:ring-2 focus:ring-fg/30"
        >
          Skip to content
        </a>
        <div className="flex min-h-screen flex-col">
          <header className="border-b border-line bg-panel">
            <div className="mx-auto flex h-12 w-full max-w-[1600px] items-center justify-between px-6">
              <Link href="/" className="flex items-center gap-2.5">
                <span
                  aria-hidden
                  className="flex h-5 w-5 items-center justify-center rounded bg-accent text-[10px] font-bold text-panel"
                >
                  Bi
                </span>
                <span className="text-[13.5px] font-semibold tracking-[-0.01em]">BioIntel</span>
                <span className="hidden text-[13px] text-fg-3 sm:inline">
                  Scientific due diligence
                </span>
              </Link>
              <nav className="flex items-center gap-1 text-[13px]">
                <Link
                  href="/"
                  className="rounded-md px-2.5 py-1.5 text-fg-2 transition-colors hover:bg-subtle hover:text-fg"
                >
                  Decks
                </Link>
                <Link
                  href="/runs"
                  className="rounded-md px-2.5 py-1.5 text-fg-2 transition-colors hover:bg-subtle hover:text-fg"
                >
                  Analyses
                </Link>
              </nav>
            </div>
          </header>

          <main id="content" className="flex-1">
            {children}
          </main>

          <footer className="border-t border-line py-5">
            <div className="mx-auto max-w-[1600px] px-6 text-2xs leading-relaxed text-fg-3">
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
