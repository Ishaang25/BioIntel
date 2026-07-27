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
    } catch (err) {
      setError(err instanceof ApiRequestError ? err.message : 'Could not start the analysis.');
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-col items-end gap-1">
      <button className="btn-primary" onClick={start} disabled={busy}>
        {busy ? 'Starting…' : hasActiveRun ? 'Re-analyse' : 'Analyse'}
      </button>
      {error && <span className="text-xs text-red-600 dark:text-red-400">{error}</span>}
    </div>
  );
}
