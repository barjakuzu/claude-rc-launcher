/**
 * Small in-process TTL cache with request coalescing. On Vercel each instance has its
 * own cache; that is acceptable for weather/FX/charger data with short TTLs. Swap for
 * a shared store (e.g. Upstash/Redis) if provider quotas require it — see docs/providers.md.
 */
type Entry<T> = { value: T; expires: number; storedAt: number };

export class TtlCache<T> {
  private map = new Map<string, Entry<T>>();
  private inflight = new Map<string, Promise<T>>();
  constructor(private readonly max = 500) {}

  peek(key: string): Entry<T> | undefined {
    return this.map.get(key);
  }

  async get(key: string, ttlMs: number, load: () => Promise<T>): Promise<{ value: T; cached: boolean; storedAt: number }> {
    const hit = this.map.get(key);
    if (hit && hit.expires > Date.now()) return { value: hit.value, cached: true, storedAt: hit.storedAt };
    let p = this.inflight.get(key);
    if (!p) {
      p = load().finally(() => this.inflight.delete(key));
      this.inflight.set(key, p);
    }
    const value = await p;
    const storedAt = Date.now();
    this.map.delete(key);
    this.map.set(key, { value, expires: storedAt + ttlMs, storedAt });
    while (this.map.size > this.max) this.map.delete(this.map.keys().next().value!);
    return { value, cached: false, storedAt };
  }

  /** Last value even if expired — for "stale but labelled" fallbacks when a provider fails. */
  stale(key: string): Entry<T> | undefined {
    return this.map.get(key);
  }
}

/** Rounds coordinates to a grid tile so nearby users share cache entries (and we never key on exact GPS). */
export function tileKey(lat: number, lon: number, step = 0.1) {
  const r = (v: number) => (Math.round(v / step) * step).toFixed(2);
  return `${r(lat)},${r(lon)}`;
}
