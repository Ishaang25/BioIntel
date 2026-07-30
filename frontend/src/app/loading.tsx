export default function Loading() {
  return (
    <div className="page space-y-4" aria-busy="true" aria-label="Loading">
      <div className="h-7 w-56 animate-pulse rounded bg-subtle" />
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {[0, 1, 2, 3].map((index) => (
          <div key={index} className="h-24 animate-pulse rounded-card bg-subtle" />
        ))}
      </div>
      <div className="h-64 animate-pulse rounded-card bg-subtle" />
    </div>
  );
}
