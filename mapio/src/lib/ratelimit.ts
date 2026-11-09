/**
 * In-memory sliding-window limiter (per instance). Adequate as a first line of defence
 * for a single region; replace with a shared store for multi-instance hard limits.
 */
const buckets = new Map<string, number[]>();
const alerts: { key: string; at: string }[] = [];

export function rateLimit(key: string, limit: number, windowMs: number): { ok: boolean; retryAfterS: number } {
  const now = Date.now();
  const arr = (buckets.get(key) ?? []).filter((t) => t > now - windowMs);
  if (arr.length >= limit) {
    buckets.set(key, arr);
    if (alerts.length > 200) alerts.shift();
    alerts.push({ key: key.replace(/:[0-9a-f-]{36}$/, ":<user>"), at: new Date(now).toISOString() });
    return { ok: false, retryAfterS: Math.ceil((arr[0] + windowMs - now) / 1000) };
  }
  arr.push(now);
  buckets.set(key, arr);
  if (buckets.size > 10_000) buckets.delete(buckets.keys().next().value!);
  return { ok: true, retryAfterS: 0 };
}

export function recentRateLimitAlerts() {
  return alerts.slice(-50).reverse();
}
