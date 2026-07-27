'use client';

import { useEffect } from 'react';

export default function Error({ error, reset }: { error: Error; reset: () => void }) {
  useEffect(() => {
    // Surface in the browser console; the server has the full trace.
    console.error(error);
  }, [error]);

  return (
    <div className="card py-14 text-center">
      <h2 className="text-base font-semibold">Something went wrong</h2>
      <p className="mx-auto mt-2 max-w-md text-sm text-ink-500">
        {error.message || 'An unexpected error occurred while loading this page.'}
      </p>
      <button className="btn-primary mt-4" onClick={reset}>
        Try again
      </button>
    </div>
  );
}
