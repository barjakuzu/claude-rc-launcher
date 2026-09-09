// useCrossDevice.ts — cross-device data aggregation hooks.
// Polls every 8s while active === true. Uses Promise.allSettled so one
// slow/offline device never blocks the rest.
//
// Round 5: found and fixed a real bug in this hook's `mounted` ref while
// verifying useAllSchedules's new hasLoaded flag in dev. `useRef(true)`
// only sets the initial value once; the mount-tracking effect's body did
// nothing to reset it back to true on mount, only its cleanup set it to
// false. React StrictMode (dev only) mounts, cleans up, then remounts
// every component once up front to catch exactly this class of bug: the
// cleanup ran (mounted.current = false) and nothing ever set it back to
// true, so every fetchAll() after that first simulated cycle silently
// discarded its own result forever, for the real lifetime of the
// component (items, and the new hasLoaded, stuck at their initial
// values). This is dev-only in practice (a genuine production unmount
// creates a fresh ref on remount), but it made the new hasLoaded flag
// impossible to verify honestly, and the old code had the identical bug
// silently baked in: it just looked like "confirmed zero" instead of
// "stuck loading", which is exactly the failure mode this lane exists to
// remove. Fixed by setting mounted.current = true in the effect body
// itself, not just relying on the ref's one-time initializer.
//
// useAllSessions (the per-device sessions.py fan-out this file used to
// also export) was removed here: useFleet.ts replaced it as the Sessions
// tab's data source, and it had been left with zero importers ever since,
// still carrying its own unswept copy of the fabricated-zero pattern
// (returning `items` with no hasLoaded at all). Same judgment as deleting
// Grid.tsx: confirmed dead (grep for `useAllSessions` across frontend/src
// turns up only this definition), so deleted rather than left for a
// future sweep to trip over.
import { useState, useEffect, useRef } from 'react';
import { api } from './api';
import type { DeviceCard, Schedule } from './types';

// ─── ScheduleWithDevice ────────────────────────────────────────────────────────
export interface ScheduleWithDevice {
  device: DeviceCard;
  schedule: Schedule;
}

export interface UseAllSchedulesResult {
  items: ScheduleWithDevice[];
  /** True once the per-device fan-out has completed at least once for the
   * current `cards` list (Round 5: the same fabricated-zero pattern the
   * strip and AllSessions.tsx were swept for). A caller must additionally
   * confirm its own `cards` prop has itself finished loading (e.g. App.tsx's
   * hasLoadedCards) before trusting a 0 here as "confirmed none" rather
   * than "cards was still empty when this last resolved". */
  hasLoaded: boolean;
  /** Round 4: true when the most recent fan-out had at least one device
   * whose /schedules call rejected (Promise.allSettled), most commonly a
   * DeviceUnreachableError (api.ts). Those rejected results were dropped
   * from `items` with no signal at all, so hasLoaded, which only ever
   * meant "we heard back at least once, so 0 isn't fabricated," read
   * exactly like "confirmed complete" even when some devices on the
   * current `cards` list never actually got counted. `items` is still
   * real data, just possibly missing whatever those devices would have
   * contributed. */
  partial: boolean;
}

export function useAllSchedules(cards: DeviceCard[], active: boolean): UseAllSchedulesResult {
  const [items, setItems] = useState<ScheduleWithDevice[]>([]);
  const [hasLoaded, setHasLoaded] = useState(false);
  const [partial, setPartial] = useState(false);
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);

  const cardsRef = useRef(cards);
  cardsRef.current = cards;
  const key = cards.map((c) => c.id + ':' + (c.online ? 1 : 0)).join(',');

  useEffect(() => {
    // The device-list identity changed (a device added/removed, or one's
    // online status flipped) since the last time this fan-out completed:
    // that completed run only ever queried the previous set of devices,
    // so a stale hasLoaded=true must not keep asserting a "confirmed"
    // schedule count that never included whatever changed. Reset until
    // the new fetchAll below (once active) lands a fresh answer for the
    // current key.
    setHasLoaded(false);
    setPartial(false);
    if (!active) return;
    let cancelled = false;

    const fetchAll = async () => {
      const current = cardsRef.current;
      const results = await Promise.allSettled(current.map((d) => api.schedules(d.id)));
      if (cancelled || !mounted.current) return;
      const flat: ScheduleWithDevice[] = [];
      let anyRejected = false;
      results.forEach((r, i) => {
        if (r.status === 'fulfilled') {
          const ss = Array.isArray(r.value)
            ? r.value
            : ((r.value as { schedules?: Schedule[] })?.schedules ?? []);
          for (const s of ss) flat.push({ device: current[i], schedule: s });
        } else {
          anyRejected = true;
        }
      });
      setItems(flat);
      setHasLoaded(true);
      setPartial(anyRejected);
    };

    fetchAll();
    const id = setInterval(fetchAll, 8000);
    return () => { cancelled = true; clearInterval(id); };
  }, [active, key]);

  return { items, hasLoaded, partial };
}
