export default function Loading() {
  return (
    <div role="status" aria-label="Loading" className="grid gap-4">
      <div className="h-10 w-64 animate-pulse rounded-xl bg-white/5" />
      <div className="h-64 animate-pulse rounded-[var(--radius)] bg-white/5" />
    </div>
  );
}
