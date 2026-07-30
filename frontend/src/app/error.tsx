'use client';

import { useEffect } from 'react';

export default function Error({ error, reset }: { error: Error; reset: () => void }) {
  useEffect(() => {
    // Surface in the browser console; the server has the full trace.
    console.error(error);
  }, [error]);

  return (
    <div className="page">
      <div className="card flex flex-col items-center gap-3 py-16 text-center">
        <h2 className="text-[15px] font-semibold">Something went wrong</h2>
        <p className="max-w-md text-[13px] leading-relaxed text-fg-2">
          {error.message || 'An unexpected error occurred while loading this page.'}
        </p>
        <button className="btn btn-sm btn-primary mt-1" onClick={reset}>
          Try again
        </button>
      </div>
    </div>
  );
}
