// pollWithBackoff.ts: shared self-rescheduling poll loop for useAlerts.ts
// and useCost.ts. On a successful call it waits `intervalMs` before the
// next attempt and resets backoff to its base. On a failed call (the
// supplied function throws or rejects) it backs off exponentially from
// `backoffBaseMs` up to `backoffMaxMs`, instead of hammering a route that
// keeps failing (e.g. a 404, before the backend lane that serves it has
// shipped) at the full steady-state interval forever.
//
// The polled function owns its own success/failure side effects (updating
// component state, etc.) and is expected to rethrow on failure so this
// loop knows to back off; it never surfaces that rejection anywhere else
// (no unhandled rejection), it only uses it to pick the next delay.
export interface PollOptions {
  intervalMs: number;
  backoffBaseMs?: number;
  backoffMaxMs?: number;
}

export function pollWithBackoff(fn: () => Promise<void>, options: PollOptions): () => void {
  const { intervalMs, backoffBaseMs = 2_000, backoffMaxMs = 60_000 } = options;
  let cancelled = false;
  let backoff = backoffBaseMs;
  let timer: ReturnType<typeof setTimeout> | null = null;

  const tick = async () => {
    let ok = true;
    try {
      await fn();
    } catch {
      ok = false;
    }
    if (cancelled) return;
    if (ok) {
      backoff = backoffBaseMs;
      timer = setTimeout(tick, intervalMs);
    } else {
      timer = setTimeout(tick, backoff);
      backoff = Math.min(backoff * 2, backoffMaxMs);
    }
  };

  tick();

  return () => {
    cancelled = true;
    if (timer) clearTimeout(timer);
  };
}
