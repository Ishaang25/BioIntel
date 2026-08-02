'use client';

import Link from 'next/link';
import { useEffect } from 'react';

export default function Error({ error, reset }: { error: Error; reset: () => void }) {
  useEffect(() => {
    // Surface in the browser console; the server has the full trace.
    console.error(error);
  }, [error]);

  return (
    <div className="page">
      <div className="card flex flex-col items-center gap-3 py-16 text-center">
        <h2 className="text-[15px] font-semibold">This page could not be loaded</h2>
        <p className="max-w-md text-[13px] leading-relaxed text-fg-2">
          Nothing has been lost — analyses run on the server and are unaffected by this. Retrying
          usually works; if it does not, the API may be restarting.
        </p>
        {error.message && (
          <p className="max-w-md text-2xs leading-relaxed text-fg-3">{error.message}</p>
        )}
        <div className="mt-1 flex items-center gap-2">
          <button className="btn btn-sm btn-primary" onClick={reset}>
            Try again
          </button>
          <Link className="btn btn-sm btn-ghost" href="/">
            Back to decks
          </Link>
        </div>
      </div>
    </div>
  );
}
