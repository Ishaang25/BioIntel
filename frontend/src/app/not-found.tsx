import Link from 'next/link';

import { EmptyState } from '@/components/ui';

export default function NotFound() {
  return (
    <div className="page">
      <EmptyState
        title="Not found"
        description="That document or analysis does not exist. It may have been deleted."
        action={
          <Link href="/" className="btn btn-sm btn-primary">
            Back to decks
          </Link>
        }
      />
    </div>
  );
}
