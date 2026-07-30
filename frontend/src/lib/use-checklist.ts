'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';

/**
 * A checklist whose state lives in the browser.
 *
 * Diligence progress is a working note, not an analysis artefact, so it is
 * deliberately not written back to the API: the report stays reproducible, and
 * two analysts reading the same run do not overwrite each other's ticks. State
 * is keyed by run so switching between analyses does not mix them up.
 */
export function useChecklist(storageKey: string) {
  const [done, setDone] = useState<ReadonlySet<string>>(() => new Set());
  const [hydrated, setHydrated] = useState(false);

  // Read after mount: localStorage is unavailable during server rendering, and
  // seeding it in `useState` would produce a hydration mismatch.
  useEffect(() => {
    try {
      const raw = window.localStorage.getItem(storageKey);
      if (raw) {
        const parsed: unknown = JSON.parse(raw);
        if (Array.isArray(parsed)) {
          setDone(new Set(parsed.filter((value): value is string => typeof value === 'string')));
        }
      }
    } catch {
      /* private mode, quota, or corrupt value — start empty */
    }
    setHydrated(true);
  }, [storageKey]);

  const persist = useCallback(
    (next: ReadonlySet<string>) => {
      setDone(next);
      try {
        window.localStorage.setItem(storageKey, JSON.stringify([...next]));
      } catch {
        /* nothing we can do; the UI still reflects the change this session */
      }
    },
    [storageKey],
  );

  const toggle = useCallback(
    (id: string) => {
      setDone((current) => {
        const next = new Set(current);
        if (next.has(id)) next.delete(id);
        else next.add(id);
        try {
          window.localStorage.setItem(storageKey, JSON.stringify([...next]));
        } catch {
          /* ignore */
        }
        return next;
      });
    },
    [storageKey],
  );

  const clear = useCallback(() => persist(new Set()), [persist]);

  return useMemo(
    () => ({ done, hydrated, toggle, clear }),
    [done, hydrated, toggle, clear],
  );
}
