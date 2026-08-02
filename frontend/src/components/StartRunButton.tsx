'use client';

import { useRouter } from 'next/navigation';
import { useState } from 'react';

import { api, ApiRequestError } from '@/lib/api';

export function StartRunButton({
  documentId,
  hasActiveRun,
}: {
  documentId: string;
  hasActiveRun: boolean;
}) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function start() {
    setBusy(true);
    setError(null);
    try {
      // `force` re-analyses even when a run is already in flight, which is the
      // intent when the button is deliberately labelled "Re-analyse".
      const run = await api.createRun(documentId, hasActiveRun);
      router.push(`/runs/${run.id}`);
      router.refresh();
    } catch (cause) {
      setError(cause instanceof ApiRequestError ? cause.message : 'Could not start the analysis.');
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-col items-end gap-1">
      <button
        className="btn btn-primary"
        onClick={start}
        disabled={busy}
        title={
          hasActiveRun
            ? 'Start a second analysis of this deck. The one in progress keeps running.'
            : 'Extract the claims, check them against the literature and write the memo. A few minutes.'
        }
      >
        {busy ? 'Starting…' : hasActiveRun ? 'Analyse again' : 'Analyse this deck'}
      </button>
      {error ? (
        <span role="alert" className="text-2xs text-crit">
          {error}
        </span>
      ) : (
        <span className="text-2xs text-fg-3">Takes a few minutes</span>
      )}
    </div>
  );
}
