// relativeTime.ts — shared "Xm ago" formatter for the store's unix-epoch-
// seconds timestamps (last_seen, ts, started_at, ...). Distinct from
// Activity.tsx's relativeTime(), which formats ISO date strings instead.
export function formatRelativeTime(epochSeconds: number | null | undefined): string {
  if (epochSeconds == null) return 'unknown';
  const diffMs = Date.now() - epochSeconds * 1000;
  if (isNaN(diffMs)) return 'unknown';
  const mins = Math.floor(diffMs / 60_000);
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  if (days === 1) return 'yesterday';
  return `${days}d ago`;
}
