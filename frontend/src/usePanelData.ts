// usePanelData.ts — shared data-fetching hook for device detail views.
import { useState, useEffect, useCallback } from 'react';
import { api } from './api';
import type { Session, Schedule } from './types';
import type { PanelTab } from './components/PanelTabs';

export function usePanelData(deviceId: string, tab: PanelTab) {
  const [sessions, setSessions]   = useState<Session[]>([]);
  const [scheduled, setScheduled] = useState<Schedule[]>([]);

  const fetchSessions = useCallback(async () => {
    try {
      const data = await api.sessions(deviceId);
      const arr: Session[] = Array.isArray(data) ? data : (data?.sessions ?? []);
      setSessions(arr);
    } catch {/* ignore */}
  }, [deviceId]);

  const fetchScheduled = useCallback(async () => {
    try {
      const data = await api.schedules(deviceId);
      const arr: Schedule[] = Array.isArray(data) ? data : (data?.schedules ?? []);
      setScheduled(arr);
    } catch {/* ignore */}
  }, [deviceId]);

  // Poll sessions every 4 s.
  useEffect(() => {
    let cancelled = false;
    fetchSessions();
    const id = setInterval(() => { if (!cancelled) fetchSessions(); }, 4000);
    return () => { cancelled = true; clearInterval(id); };
  }, [fetchSessions]);

  // Fetch schedules on open + when tab switches to scheduled.
  useEffect(() => {
    if (tab === 'scheduled') fetchScheduled();
  }, [tab, fetchScheduled]);

  return { sessions, scheduled, reloadSessions: fetchSessions, reloadSchedules: fetchScheduled };
}
