import Link from 'next/link';

import { EmptyState } from '@/components/ui';

export default function NotFound() {
  return (
    <div className="page">
      <EmptyState
        title="Not found"
        description="That document or analysis does not exist — it was deleted, or the link is wrong. Anything still on record is listed under Decks and Analyses."
        action={
          <div className="flex items-center gap-2">
            <Link href="/" className="btn btn-sm btn-primary">
              Back to decks
            </Link>
            <Link href="/runs" className="btn btn-sm btn-ghost">
              All analyses
            </Link>
          </div>
        }
      />
    </div>
  );
}
