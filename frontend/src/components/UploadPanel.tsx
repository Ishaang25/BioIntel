'use client';

import { useRouter } from 'next/navigation';
import { useCallback, useRef, useState } from 'react';

import {
  API_UPLOAD_LIMIT_BYTES,
  UploadError,
  formatMegabytes,
  tooLargeMessage,
  uploadDocument,
  type UploadFailureKind,
} from '@/lib/upload';
import { Card, Meter, cx } from '@/components/ui';

const MAX_BYTES = API_UPLOAD_LIMIT_BYTES;

/** What the reader should do about each way an upload can fail. */
const RECOVERY: Record<UploadFailureKind, string | null> = {
  too_large:
    'Most decks shrink well below the limit when images are downsampled — "Reduce File Size" in Preview, or "Compress PDF" in Acrobat.',
  unsupported_file: 'BioIntel reads text and scanned PDFs. Other formats need exporting to PDF first.',
  rate_limited: null,
  network: null,
  timeout: 'A smaller file will also upload faster.',
  cancelled: null,
  server: null,
};

type Phase =
  | { kind: 'idle' }
  | { kind: 'uploading'; fraction: number | null }
  | { kind: 'starting' };

export function UploadPanel() {
  const router = useRouter();
  const inputRef = useRef<HTMLInputElement>(null);
  const abortRef = useRef<AbortController | null>(null);

  const [file, setFile] = useState<File | null>(null);
  const [notes, setNotes] = useState('');
  const [dragging, setDragging] = useState(false);
  const [phase, setPhase] = useState<Phase>({ kind: 'idle' });
  const [error, setError] = useState<{ message: string; recovery: string | null } | null>(null);

  const busy = phase.kind !== 'idle';

  const accept = useCallback((candidate: File | undefined) => {
    setError(null);
    if (!candidate) return;
    if (!candidate.name.toLowerCase().endsWith('.pdf') && candidate.type !== 'application/pdf') {
      setError({
        message: 'Only PDF files are supported.',
        recovery: RECOVERY.unsupported_file,
      });
      return;
    }
    if (candidate.size === 0) {
      setError({ message: 'That file is empty.', recovery: null });
      return;
    }
    if (candidate.size > MAX_BYTES) {
      setError({ message: tooLargeMessage(MAX_BYTES), recovery: RECOVERY.too_large });
      return;
    }
    setFile(candidate);
  }, []);

  async function submit() {
    if (!file || busy) return;
    const controller = new AbortController();
    abortRef.current = controller;
    setPhase({ kind: 'uploading', fraction: 0 });
    setError(null);

    try {
      const result = await uploadDocument(file, {
        notes,
        analyze: true,
        signal: controller.signal,
        onProgress: ({ fraction }) => setPhase({ kind: 'uploading', fraction }),
      });
      // The bytes are in; what follows is the API creating the run. Saying so
      // keeps the panel honest during the pause between the two.
      setPhase({ kind: 'starting' });
      if (result.run) router.push(`/runs/${result.run.id}`);
      else router.push(`/documents/${result.document.id}`);
      router.refresh();
    } catch (cause) {
      abortRef.current = null;
      if (cause instanceof UploadError) {
        if (cause.kind === 'cancelled') {
          setPhase({ kind: 'idle' });
          return;
        }
        setError({ message: cause.message, recovery: RECOVERY[cause.kind] });
      } else {
        setError({
          message: 'The upload failed before it reached BioIntel.',
          recovery: 'Check that the backend is running, then try again.',
        });
      }
      setPhase({ kind: 'idle' });
    }
  }

  const percent =
    phase.kind === 'uploading' && phase.fraction !== null ? Math.round(phase.fraction * 100) : null;

  return (
    <Card className="p-6">
      <h2 className="text-[15px] font-semibold tracking-[-0.01em]">Analyse a pitch deck</h2>
      <p className="mt-1.5 text-[13px] leading-relaxed text-fg-2">
        Upload a biotech pitch deck as a PDF. Scanned decks are read visually; charts and tables are
        interpreted alongside the text.
      </p>

      <div
        onDragOver={(event) => {
          if (busy) return;
          event.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(event) => {
          if (busy) return;
          event.preventDefault();
          setDragging(false);
          accept(event.dataTransfer.files[0]);
        }}
        onClick={() => !busy && inputRef.current?.click()}
        onKeyDown={(event) => {
          if (busy) return;
          if (event.key === 'Enter' || event.key === ' ') {
            event.preventDefault();
            inputRef.current?.click();
          }
        }}
        role="button"
        tabIndex={busy ? -1 : 0}
        aria-disabled={busy}
        aria-label="Choose a PDF to analyse"
        className={cx(
          'mt-5 rounded-card border border-dashed px-6 py-12 text-center transition-colors',
          busy && 'cursor-default opacity-70',
          !busy && 'cursor-pointer',
          dragging
            ? 'border-fg bg-subtle'
            : 'border-line-strong hover:border-fg-3 hover:bg-subtle/60',
        )}
      >
        <input
          ref={inputRef}
          type="file"
          accept="application/pdf,.pdf"
          className="hidden"
          onChange={(event) => accept(event.target.files?.[0])}
        />
        {file ? (
          <>
            <div className="text-[13.5px] font-medium text-fg">{file.name}</div>
            <div className="num mt-1 text-2xs text-fg-3">
              {formatMegabytes(file.size)} MB
              {!busy && ' — click to choose a different file'}
            </div>
          </>
        ) : (
          <>
            <div className="text-[13.5px] font-medium text-fg">
              Drop a PDF here, or click to browse
            </div>
            <div className="mt-1 text-2xs text-fg-3">
              Up to {formatMegabytes(MAX_BYTES)} MB, digital or scanned
            </div>
          </>
        )}
      </div>

      <label className="mt-4 block">
        <span className="label">Context for the analyst (optional)</span>
        <textarea
          value={notes}
          onChange={(event) => setNotes(event.target.value)}
          rows={2}
          disabled={busy}
          placeholder="e.g. Series A, introduced by a co-investor; focus on the translational story."
          className="field mt-1.5 resize-y disabled:opacity-60"
        />
      </label>

      {busy && (
        <div className="mt-4" aria-live="polite">
          <div className="flex items-baseline justify-between gap-2 text-[13px]">
            <span className="text-fg">
              {phase.kind === 'starting'
                ? 'Upload complete — starting the analysis…'
                : 'Sending the deck to BioIntel…'}
            </span>
            {percent !== null && <span className="num text-fg-3">{percent}%</span>}
          </div>
          <Meter
            className="mt-2"
            value={phase.kind === 'starting' ? 100 : (percent ?? 8)}
            tone="info"
            label="Upload progress"
          />
          <p className="mt-2 text-2xs text-fg-3">
            {phase.kind === 'starting'
              ? 'Queuing the run. You will be taken to its live progress page.'
              : 'Keep this tab open until the upload finishes. Analysis itself continues without it.'}
          </p>
        </div>
      )}

      {error && (
        <div
          role="alert"
          className="mt-3 rounded-lg border border-crit/25 bg-crit/10 px-3.5 py-2.5 text-[13px] leading-relaxed text-crit"
        >
          {error.message}
          {error.recovery && <p className="mt-1.5 text-fg-2">{error.recovery}</p>}
        </div>
      )}

      <div className="mt-5 flex items-center gap-2">
        <button className="btn btn-primary" onClick={submit} disabled={!file || busy}>
          {busy ? 'Uploading…' : 'Upload and analyse'}
        </button>
        {busy ? (
          <button
            className="btn btn-ghost"
            onClick={() => abortRef.current?.abort()}
            disabled={phase.kind === 'starting'}
          >
            Cancel
          </button>
        ) : (
          file && (
            <button
              className="btn btn-ghost"
              onClick={() => {
                setFile(null);
                setError(null);
              }}
            >
              Clear
            </button>
          )
        )}
      </div>
    </Card>
  );
}
