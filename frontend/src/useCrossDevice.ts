// useCrossDevice.ts — cross-device data aggregation hooks.
// Each hook polls every 5s (sessions) or 8s (schedules) while active === true.
// Uses Promise.allSettled so one slow/offline device never blocks the rest.
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
  useEffect(() => () => { mounted.current = false; }, []);

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

export function useAllSchedules(cards: DeviceCard[], active: boolean): ScheduleWithDevice[] {
  const [items, setItems] = useState<ScheduleWithDevice[]>([]);
  const mounted = useRef(true);
  useEffect(() => () => { mounted.current = false; }, []);

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
    };

    fetchAll();
    const id = setInterval(fetchAll, 8000);
    return () => { cancelled = true; clearInterval(id); };
  }, [active, key]);

  return items;
}
