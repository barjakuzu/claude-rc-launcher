// useNow.ts: a re-render tick, shared by any countdown display. Deliberately
// exposes nothing but "the clock moved" — callers recompute their own
// countdown text from a source ISO timestamp on every tick (limitsFormat.ts)
// rather than decrementing a stored duration, so the display can never drift
// from the real reset time (CONTRACT.md section 6).
import { useEffect, useState } from 'react';

export function useNow(intervalMs = 30_000): number {
  const [now, setNow] = useState<number>(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), intervalMs);
    return () => clearInterval(id);
  }, [intervalMs]);
  return now;
}
