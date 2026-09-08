// useCrossDevice.ts — cross-device data aggregation hooks.
// Each hook polls every 5s (sessions) or 8s (schedules) while active === true.
// Uses Promise.allSettled so one slow/offline device never blocks the rest.
//
// Round 5: found and fixed a real bug in both hooks' `mounted` ref while
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
import { useState, useEffect, useRef } from 'react';
import { api } from './api';
import type { DeviceCard, Session, Schedule } from './types';

// ─── SessionWithDevice ─────────────────────────────────────────────────────────
export interface SessionWithDevice {
  device: DeviceCard;
  session: Session;
}

export function useAllSessions(cards: DeviceCard[], active: boolean): SessionWithDevice[] {
  const [items, setItems] = useState<SessionWithDevice[]>([]);
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);

  const cardsRef = useRef(cards);
  cardsRef.current = cards;
  const key = cards.map((c) => c.id + ':' + (c.online ? 1 : 0)).join(',');

  useEffect(() => {
    if (!active) return;
    let cancelled = false;

    const fetchAll = async () => {
      const online = cardsRef.current.filter((c) => c.online);
      const results = await Promise.allSettled(online.map((d) => api.sessions(d.id)));
      if (cancelled || !mounted.current) return;
      const flat: SessionWithDevice[] = [];
      results.forEach((r, i) => {
        if (r.status === 'fulfilled') {
          const sessions = Array.isArray(r.value)
            ? r.value
            : ((r.value as { sessions?: Session[] })?.sessions ?? []);
          for (const s of sessions) flat.push({ device: online[i], session: s });
        }
      });
      setItems(flat);
    };

    fetchAll();
    const id = setInterval(fetchAll, 5000);
    return () => { cancelled = true; clearInterval(id); };
  }, [active, key]);

  return items;
}

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
}

export function useAllSchedules(cards: DeviceCard[], active: boolean): UseAllSchedulesResult {
  const [items, setItems] = useState<ScheduleWithDevice[]>([]);
  const [hasLoaded, setHasLoaded] = useState(false);
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);

  const cardsRef = useRef(cards);
  cardsRef.current = cards;
  const key = cards.map((c) => c.id + ':' + (c.online ? 1 : 0)).join(',');

  useEffect(() => {
    if (!active) return;
    let cancelled = false;

    const fetchAll = async () => {
      const current = cardsRef.current;
      const results = await Promise.allSettled(current.map((d) => api.schedules(d.id)));
      if (cancelled || !mounted.current) return;
      const flat: ScheduleWithDevice[] = [];
      results.forEach((r, i) => {
        if (r.status === 'fulfilled') {
          const ss = Array.isArray(r.value)
            ? r.value
            : ((r.value as { schedules?: Schedule[] })?.schedules ?? []);
          for (const s of ss) flat.push({ device: current[i], schedule: s });
        }
      });
      setItems(flat);
      setHasLoaded(true);
    };

    fetchAll();
    const id = setInterval(fetchAll, 8000);
    return () => { cancelled = true; clearInterval(id); };
  }, [active, key]);

  return { items, hasLoaded };
}
