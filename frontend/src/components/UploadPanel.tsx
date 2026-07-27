'use client';

import { useRouter } from 'next/navigation';
import { useCallback, useRef, useState } from 'react';

import { api, ApiRequestError } from '@/lib/api';

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
      setError(`That file is ${(candidate.size / 1_048_576).toFixed(1)} MB; the limit is ${MAX_MB} MB.`);
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
      if (result.run) {
        router.push(`/runs/${result.run.id}`);
      } else {
        router.push(`/documents/${result.document.id}`);
      }
      router.refresh();
    } catch (err) {
      setError(
        err instanceof ApiRequestError
          ? err.message
          : 'Upload failed. Check that the backend is running.',
      );
      setBusy(false);
    }
  }

  return (
    <div className="card">
      <h2 className="text-base font-semibold">Analyse a pitch deck</h2>
      <p className="mt-1 text-sm text-ink-500">
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
          if (event.key === 'Enter' || event.key === ' ') inputRef.current?.click();
        }}
        role="button"
        tabIndex={0}
        aria-label="Choose a PDF to analyse"
        className={`mt-4 cursor-pointer rounded-lg border-2 border-dashed px-6 py-10 text-center transition-colors ${
          dragging
            ? 'border-ink-900 bg-ink-100 dark:border-ink-100 dark:bg-ink-800'
            : 'border-ink-300 hover:border-ink-400 dark:border-ink-700 dark:hover:border-ink-600'
        }`}
      >
        <input
          ref={inputRef}
          type="file"
          accept="application/pdf,.pdf"
          className="hidden"
          onChange={(event) => accept(event.target.files?.[0])}
        />
        {file ? (
          <div>
            <div className="text-sm font-medium">{file.name}</div>
            <div className="mt-1 text-xs text-ink-500">
              {(file.size / 1_048_576).toFixed(1)} MB — click to choose a different file
            </div>
          </div>
        ) : (
          <div>
            <div className="text-sm font-medium">Drop a PDF here, or click to browse</div>
            <div className="mt-1 text-xs text-ink-500">Up to {MAX_MB} MB, digital or scanned</div>
          </div>
        )}
      </div>

      <label className="mt-4 block">
        <span className="label">Context for the analyst (optional)</span>
        <textarea
          value={notes}
          onChange={(event) => setNotes(event.target.value)}
          rows={2}
          placeholder="e.g. Series A, introduced by a co-investor; focus on the translational story."
          className="mt-1 w-full rounded-md border border-ink-300 bg-white px-3 py-2 text-sm
                     placeholder:text-ink-400 focus:border-ink-500 focus:outline-none
                     dark:border-ink-700 dark:bg-ink-900"
        />
      </label>

      {error && (
        <div
          role="alert"
          className="mt-3 rounded-md border border-red-500/40 bg-red-500/5 px-3 py-2 text-sm text-red-700 dark:text-red-400"
        >
          {error}
        </div>
      )}

      <div className="mt-4 flex items-center gap-3">
        <button className="btn-primary" onClick={submit} disabled={!file || busy}>
          {busy ? 'Uploading…' : 'Upload and analyse'}
        </button>
        {file && !busy && (
          <button
            className="btn-secondary"
            onClick={() => {
              setFile(null);
              setError(null);
            }}
          >
            Clear
          </button>
        )}
      </div>
    </div>
  );
}
