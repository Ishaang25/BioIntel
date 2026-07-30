'use client';

import { useRef, useState } from 'react';

import { api } from '@/lib/api';
import { exportReportPdf } from '@/lib/pdf';
import type { ReportModel } from '@/lib/report-model';
import { useDismiss } from '@/components/ui/Floating';
import { cx } from '@/components/ui/primitives';

/**
 * Export controls.
 *
 * The PDF is the deliverable and gets the primary action; the API's markdown
 * and HTML exports stay available behind the secondary menu because downstream
 * tooling already consumes them.
 */
export function ExportMenu({ model }: { model: ReportModel }) {
  const [busy, setBusy] = useState(false);
  const [open, setOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const menuRef = useRef<HTMLDivElement>(null);

  useDismiss(open, () => setOpen(false), [menuRef.current]);

  async function downloadPdf() {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      await exportReportPdf(model);
    } catch (cause) {
      console.error(cause);
      setError('The PDF could not be generated.');
    } finally {
      setBusy(false);
    }
  }

  return (
    <div ref={menuRef} className="relative flex items-center gap-2">
      {error && <span className="text-2xs text-crit">{error}</span>}

      <button type="button" className="btn btn-sm btn-primary" onClick={downloadPdf} disabled={busy}>
        {busy ? (
          <>
            <Spinner />
            Preparing…
          </>
        ) : (
          <>
            <DownloadIcon />
            Export PDF
          </>
        )}
      </button>

      <button
        type="button"
        className="btn btn-sm btn-secondary"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
      >
        More
        <svg
          width="9"
          height="9"
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

      {open && (
        <div
          role="menu"
          className="absolute right-0 top-full z-30 mt-1.5 w-56 overflow-hidden rounded-lg border border-line bg-panel py-1 shadow-pop"
        >
          <a
            role="menuitem"
            className="flex flex-col px-3 py-2 text-[13px] transition-colors hover:bg-subtle"
            href={api.reportExportUrl(model.runId, 'html')}
            target="_blank"
            rel="noreferrer"
            onClick={() => setOpen(false)}
          >
            <span className="font-medium text-fg">Open HTML memo</span>
            <span className="text-2xs text-fg-3">Server-rendered, printable</span>
          </a>
          <a
            role="menuitem"
            className="flex flex-col px-3 py-2 text-[13px] transition-colors hover:bg-subtle"
            href={api.reportExportUrl(model.runId, 'markdown')}
            download
            onClick={() => setOpen(false)}
          >
            <span className="font-medium text-fg">Download Markdown</span>
            <span className="text-2xs text-fg-3">For downstream tooling</span>
          </a>
        </div>
      )}
    </div>
  );
}

function DownloadIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 14 14" aria-hidden>
      <path
        d="M7 1.5v8m0 0L4 6.75M7 9.5l3-2.75M1.75 11.5h10.5"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function Spinner() {
  return (
    <svg width="12" height="12" viewBox="0 0 16 16" aria-hidden className="animate-spin">
      <circle cx="8" cy="8" r="6.5" fill="none" stroke="currentColor" strokeWidth="2" opacity="0.25" />
      <path
        d="M8 1.5a6.5 6.5 0 016.5 6.5"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
      />
    </svg>
  );
}
