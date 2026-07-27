export default function Loading() {
  return (
    <div className="space-y-4" aria-busy="true" aria-label="Loading">
      <div className="h-8 w-64 animate-pulse rounded bg-ink-200 dark:bg-ink-800" />
      <div className="h-32 animate-pulse rounded-lg bg-ink-200 dark:bg-ink-800" />
      <div className="h-64 animate-pulse rounded-lg bg-ink-200 dark:bg-ink-800" />
    </div>
  );
}
