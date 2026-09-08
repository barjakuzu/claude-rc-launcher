// useNow.ts: a re-render tick, shared by any countdown display. Deliberately
// exposes nothing but "the clock moved": callers recompute their own
// countdown text from a source ISO timestamp on every tick (limitsFormat.ts)
// rather than decrementing a stored duration, so the display can never drift
// from the real reset time (CONTRACT.md section 6).
//
// Round 4, Important 4: browsers throttle or fully suspend setInterval in a
// backgrounded tab, so a phone locked or switched away from for a while and
// then resumed sees `now` still holding whatever it was at suspension,
// stale until the next tick eventually fires (which itself may be delayed
// by the same throttling). For a mobile-first app, "resumed from
// suspension" is the common case, not an edge case: this listens for the
// tab becoming visible again and refreshes `now` immediately rather than
// waiting on the timer, so a countdown or a derived staleness age
// (limitsFormat.ts's deriveAgeSeconds) reflects the real elapsed time the
// moment the user looks at the screen again, not whenever the throttled
// interval next happens to fire.
import { useEffect, useState } from 'react';

export function useNow(intervalMs = 30_000): number {
  const [now, setNow] = useState<number>(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), intervalMs);
    const onVisible = () => {
      if (document.visibilityState === 'visible') setNow(Date.now());
    };
    document.addEventListener('visibilitychange', onVisible);
    return () => {
      clearInterval(id);
      document.removeEventListener('visibilitychange', onVisible);
    };
  }, [intervalMs]);
  return now;
}
