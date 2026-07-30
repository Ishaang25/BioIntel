'use client';

import { useRouter } from 'next/navigation';
import { useCallback, useRef, useState } from 'react';

import { api, ApiRequestError } from '@/lib/api';
import { Card, cx } from '@/components/ui';

const MAX_MB = 50;

export function UploadPanel() {
  const router = useRouter();
  const inputRef = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [notes, setNotes] = useState('');
  const [dragging, setDragging] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const accept = useCallback((candidate: File | undefined) => {
    setError(null);
    if (!candidate) return;
    if (!candidate.name.toLowerCase().endsWith('.pdf') && candidate.type !== 'application/pdf') {
      setError('Only PDF files are supported.');
      return;
    }
    if (candidate.size > MAX_MB * 1024 * 1024) {
      setError(
        `That file is ${(candidate.size / 1_048_576).toFixed(1)} MB; the limit is ${MAX_MB} MB.`,
      );
      return;
    }
    setFile(candidate);
  }, []);

  async function submit() {
    if (!file || busy) return;
    setBusy(true);
    setError(null);
    try {
      const result = await api.uploadDocument(file, notes, true);
      if (result.run) router.push(`/runs/${result.run.id}`);
      else router.push(`/documents/${result.document.id}`);
      router.refresh();
    } catch (cause) {
      setError(
        cause instanceof ApiRequestError
          ? cause.message
          : 'Upload failed. Check that the backend is running.',
      );
      setBusy(false);
    }
  }

  return (
    <Card className="p-6">
      <h2 className="text-[15px] font-semibold tracking-[-0.01em]">Analyse a pitch deck</h2>
      <p className="mt-1.5 text-[13px] leading-relaxed text-fg-2">
        Upload a biotech pitch deck as a PDF. Scanned decks are read visually; charts and tables are
        interpreted alongside the text.
      </p>

      <div
        onDragOver={(event) => {
          event.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(event) => {
          event.preventDefault();
          setDragging(false);
          accept(event.dataTransfer.files[0]);
        }}
        onClick={() => inputRef.current?.click()}
        onKeyDown={(event) => {
          if (event.key === 'Enter' || event.key === ' ') {
            event.preventDefault();
            inputRef.current?.click();
          }
        }}
        role="button"
        tabIndex={0}
        aria-label="Choose a PDF to analyse"
        className={cx(
          'mt-5 cursor-pointer rounded-card border border-dashed px-6 py-12 text-center transition-colors',
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
              {(file.size / 1_048_576).toFixed(1)} MB — click to choose a different file
            </div>
          </>
        ) : (
          <>
            <div className="text-[13.5px] font-medium text-fg">
              Drop a PDF here, or click to browse
            </div>
            <div className="mt-1 text-2xs text-fg-3">Up to {MAX_MB} MB, digital or scanned</div>
          </>
        )}
      </div>

      <label className="mt-4 block">
        <span className="label">Context for the analyst (optional)</span>
        <textarea
          value={notes}
          onChange={(event) => setNotes(event.target.value)}
          rows={2}
          placeholder="e.g. Series A, introduced by a co-investor; focus on the translational story."
          className="field mt-1.5 resize-y"
        />
      </label>

      {error && (
        <div
          role="alert"
          className="mt-3 rounded-lg border border-crit/25 bg-crit/10 px-3.5 py-2.5 text-[13px] text-crit"
        >
          {error}
        </div>
      )}

      <div className="mt-5 flex items-center gap-2">
        <button className="btn btn-primary" onClick={submit} disabled={!file || busy}>
          {busy ? 'Uploading…' : 'Upload and analyse'}
        </button>
        {file && !busy && (
          <button
            className="btn btn-ghost"
            onClick={() => {
              setFile(null);
              setError(null);
            }}
          >
            Clear
          </button>
        )}
      </div>
    </Card>
  );
}
