'use client';

import { useCallback, useEffect, useRef, useState } from 'react';

import { api } from './api';
import { progressFromRun } from './run-data';
import type { ProgressEvent, RunDetail, RunStatus } from './types';

const TERMINAL: RunStatus[] = ['succeeded', 'failed', 'cancelled'];

/** Polling cadence when the event stream is unavailable. */
const POLL_INTERVAL_MS = 4_000;

export function isTerminal(status: RunStatus | undefined): boolean {
  return status !== undefined && TERMINAL.includes(status);
}

export interface RunStream {
  run: RunDetail | null;
  progress: ProgressEvent | null;
  /** True while the server-sent event stream is live. */
  streaming: boolean;
  error: string | null;
}

/**
 * Follows a run to completion, entirely in the browser.
 *
 * Previously this lived in the progress component and advanced by calling
 * `router.refresh()`, which re-ran the Server Component on every tick and on
 * completion — dragging the full report fetch back onto the server at the exact
 * moment the backend was busiest. Progress is client state; it is tracked here
 * and nothing on the server is asked to re-render.
 *
 * The event stream is the fast path. Hosts that cap function duration will cut
 * it mid-run, so a poll takes over whenever the stream is not connected — the
 * run still finishes, just with a coarser tick.
 */
export function useRunStream(runId: string, initialRun: RunDetail | null): RunStream {
  const [run, setRun] = useState<RunDetail | null>(initialRun);
  const [progress, setProgress] = useState<ProgressEvent | null>(
    initialRun ? progressFromRun(initialRun) : null,
  );
  const [streaming, setStreaming] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const status = progress?.status;
  const done = isTerminal(status);

  /** Pulls the full run record; the stream carries progress but not metadata. */
  const refreshRun = useCallback(async () => {
    try {
      const next = await api.getRun(runId);
      setRun(next);
      setProgress((current) =>
        // A live stream is more current than a poll; do not regress it.
        current && !isTerminal(next.status) && current.status === next.status
          ? current
          : progressFromRun(next),
      );
      setError(null);
      return next;
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not reach the API.');
      return null;
    }
  }, [runId]);

  // The server may have handed us nothing (a cold or busy backend). Fetch it.
  useEffect(() => {
    if (initialRun) return;
    void refreshRun();
  }, [initialRun, refreshRun]);

  // Server-sent events: the fast path.
  useEffect(() => {
    if (done) return;

    const source = new EventSource(`/api/proxy/runs/${runId}/events`);
    let closed = false;

    const onProgress = (event: MessageEvent<string>) => {
      try {
        setProgress(JSON.parse(event.data) as ProgressEvent);
        setError(null);
      } catch {
        /* ignore a malformed frame; the next one will be fine */
      }
    };

    source.addEventListener('progress', onProgress);
    source.addEventListener('done', onProgress);
    source.onopen = () => setStreaming(true);
    source.onerror = () => {
      setStreaming(false);
      // The stream is cut when a host's function duration expires. That is
      // expected here, not an error worth showing: polling takes over.
      if (!closed) source.close();
    };

    return () => {
      closed = true;
      source.close();
      setStreaming(false);
    };
  }, [runId, done]);

  // Polling fallback, and the safety net if the stream never connects.
  useEffect(() => {
    if (done || streaming) return;
    const timer = setInterval(() => void refreshRun(), POLL_INTERVAL_MS);
    return () => clearInterval(timer);
  }, [done, streaming, refreshRun]);

  // On reaching a terminal state, pull the run once more so the header shows
  // the final duration and counts rather than the values it started with.
  const settled = useRef(false);
  useEffect(() => {
    if (!done || settled.current) return;
    settled.current = true;
    void refreshRun();
  }, [done, refreshRun]);

  return { run, progress, streaming, error };
}
