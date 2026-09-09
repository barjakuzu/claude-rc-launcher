// usePanelData.ts — shared data-fetching hook for device detail views.
import { useState, useEffect, useCallback } from 'react';
import { api, DeviceUnreachableError } from './api';
import type { Session, Schedule } from './types';
import type { PanelTab } from './components/PanelTabs';

export function usePanelData(deviceId: string, tab: PanelTab) {
  const [sessions, setSessions]   = useState<Session[]>([]);
  const [scheduled, setScheduled] = useState<Schedule[]>([]);
  // Same shape as useFleet's hasLoaded (hooks/useFleet.ts): starts false,
  // flips true only once a real response has actually landed, never flips
  // back. Sessions and scheduled arrive from two independent fetches on
  // two different cadences, so each gets its own flag rather than one
  // combined signal - a caller that needs both loaded (as AllScheduled.tsx
  // already does for its two independent sources) can AND them itself.
  // Before this, DeviceDetail/PanelTabs rendered "No active sessions",
  // "Device offline." and counts of 0 the instant the panel mounted, on
  // the initial [] state, before the first fetch had a chance to resolve.
  const [hasLoadedSessions, setHasLoadedSessions] = useState(false);
  const [hasLoadedScheduled, setHasLoadedScheduled] = useState(false);

  // Switching to a different device (DeviceRail, or opening one device
  // detail straight from another without closing) keeps this hook
  // mounted with a new deviceId rather than remounting it. Without this
  // reset, the previous device's sessions/scheduled/hasLoaded would keep
  // rendering - now confidently mislabeled as the new device's confirmed
  // state - until the new fetches resolve.
  useEffect(() => {
    setSessions([]);
    setScheduled([]);
    setHasLoadedSessions(false);
    setHasLoadedScheduled(false);
  }, [deviceId]);

  const fetchSessions = useCallback(async () => {
    try {
      const data = await api.sessions(deviceId);
      const arr: Session[] = Array.isArray(data) ? data : (data?.sessions ?? []);
      setSessions(arr);
      setHasLoadedSessions(true);
    } catch (err) {
      if (err instanceof DeviceUnreachableError) {
        // A confirmed "the hub cannot reach this device" IS an answer,
        // distinct from "we haven't heard back yet" -- unlike a generic
        // network failure (below), this must flip hasLoaded so
        // DeviceDetail.tsx's device.online branch (from the separate
        // overview poll) becomes reachable instead of hanging on
        // "Loading sessions…" forever. Round 3 fixed the over-correction
        // from round 2's fifth-door fix, which had every error respond
        // the same way this one still does for anything else: keep
        // whatever was last known, don't claim we've loaded.
        setSessions([]);
        setHasLoadedSessions(true);
      }
    }
  }, [deviceId]);

  const fetchScheduled = useCallback(async () => {
    try {
      const data = await api.schedules(deviceId);
      const arr: Schedule[] = Array.isArray(data) ? data : (data?.schedules ?? []);
      setScheduled(arr);
      setHasLoadedScheduled(true);
    } catch (err) {
      if (err instanceof DeviceUnreachableError) {
        setScheduled([]);
        setHasLoadedScheduled(true);
      }
    }
  }, [deviceId]);

  // Poll sessions every 4 s.
  useEffect(() => {
    let cancelled = false;
    fetchSessions();
    const id = setInterval(() => { if (!cancelled) fetchSessions(); }, 4000);
    return () => { cancelled = true; clearInterval(id); };
  }, [fetchSessions]);

  // Fetch schedules when device opens (deviceId changes).
  useEffect(() => {
    fetchScheduled();
  }, [deviceId, fetchScheduled]);

  // Refetch schedules when tab switches to scheduled.
  useEffect(() => {
    if (tab === 'scheduled') fetchScheduled();
  }, [tab, fetchScheduled]);

  return {
    sessions, scheduled,
    hasLoadedSessions, hasLoadedScheduled,
    reloadSessions: fetchSessions, reloadSchedules: fetchScheduled,
  };
}
