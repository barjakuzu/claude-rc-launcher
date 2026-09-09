// usePanelData.ts — shared data-fetching hook for device detail views.
import { useState, useEffect, useCallback, useRef } from 'react';
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
  // Round 4: confirmed by this hook's own direct /rc/sessions or
  // /rc/schedules probes (sessions polls every 4s, so it's normally the
  // one that gets there first), which are fresher and more authoritative
  // for "is this specific device reachable" than the separate
  // /rc/overview poll (every 5s) DeviceDetail.tsx used to defer to for
  // its "Device offline." message. Once this hook already knows, from
  // either fetch's own most recent outcome, that the device cannot be
  // reached, that must win over a staler second opinion, not merely
  // coexist with it. Round 5: originally sessions-only and named
  // sessionsUnreachable; renamed and wired into fetchScheduled too once
  // the Scheduled tab needed the same signal the Sessions tab already
  // had (DeviceDetail.tsx). Reset to false by either fetch succeeding, or
  // by a device switch.
  const [deviceUnreachable, setDeviceUnreachable] = useState(false);
  // Round 4: GET /schedules answers HTTP 200 with {"schedules": [...],
  // "error": "<schedules.LAST_LOAD_ERROR>"} when the device's own
  // schedules.json failed to parse or had invalid entries -- "schedules"
  // is still whatever validated cleanly (possibly []), never absent, so
  // this never throws and previously nothing read the "error" field at
  // all: a broken schedules file rendered as a confident "No scheduled
  // tasks on this device.", the same display that hid a real three-month
  // outage once already (schedules.json silently corrupt, the scheduler
  // a silent no-op, nobody noticed until this project's own history
  // caught it). Reset to null by a successful clean load or a device
  // switch; NOT cleared just because the list is non-empty, since
  // load_schedules() keeps whatever entries validated and only drops the
  // bad ones, so a real but incomplete list can carry this too.
  const [scheduledLoadError, setScheduledLoadError] = useState<string | null>(null);

  // Round 4: the device this hook is currently meant to be showing,
  // checked by every in-flight request before it commits a result to
  // state. fetchSessions/fetchScheduled each close over the deviceId they
  // were created for (useCallback's own dep array), but neither the 4s
  // poll interval nor a manual reload() awaits its own promise before the
  // component might switch devices out from under it: a request started
  // for device A that is still in flight when the user switches to
  // device B must not land afterward and overwrite device B's
  // already-correct state with device A's data under device B's name.
  // Wrong data attributed to the wrong machine is worse than no data on
  // a hub whose entire job is saying which machine is doing what.
  const activeDeviceRef = useRef(deviceId);

  // Switching to a different device (DeviceRail, or opening one device
  // detail straight from another without closing) keeps this hook
  // mounted with a new deviceId rather than remounting it. Without this
  // reset, the previous device's sessions/scheduled/hasLoaded would keep
  // rendering - now confidently mislabeled as the new device's confirmed
  // state - until the new fetches resolve.
  useEffect(() => {
    activeDeviceRef.current = deviceId;
    setSessions([]);
    setScheduled([]);
    setHasLoadedSessions(false);
    setHasLoadedScheduled(false);
    setDeviceUnreachable(false);
    setScheduledLoadError(null);
  }, [deviceId]);

  const fetchSessions = useCallback(async () => {
    try {
      const data = await api.sessions(deviceId);
      if (activeDeviceRef.current !== deviceId) return; // stale: device switched mid-flight
      const arr: Session[] = Array.isArray(data) ? data : (data?.sessions ?? []);
      setSessions(arr);
      setHasLoadedSessions(true);
      setDeviceUnreachable(false);
    } catch (err) {
      if (activeDeviceRef.current !== deviceId) return;
      if (err instanceof DeviceUnreachableError) {
        // A confirmed "the hub cannot reach this device" IS an answer,
        // distinct from "we haven't heard back yet" -- unlike a generic
        // network failure (below), this must flip hasLoaded so
        // DeviceDetail.tsx's offline branch becomes reachable instead of
        // hanging on "Loading sessions…" forever, and deviceUnreachable
        // so both tabs render "Device offline." from this fetch's own
        // fresher answer rather than deferring to a staler one.
        setSessions([]);
        setHasLoadedSessions(true);
        setDeviceUnreachable(true);
      }
      // Any other failure: keep whatever was last known, don't claim
      // we've loaded and don't claim we've confirmed unreachable either.
    }
  }, [deviceId]);

  const fetchScheduled = useCallback(async () => {
    try {
      const data = await api.schedules(deviceId);
      if (activeDeviceRef.current !== deviceId) return;
      const arr: Schedule[] = Array.isArray(data) ? data : (data?.schedules ?? []);
      setScheduled(arr);
      setHasLoadedScheduled(true);
      setDeviceUnreachable(false);
      const loadError = (!Array.isArray(data) && data && typeof data === 'object'
        && typeof (data as { error?: unknown }).error === 'string')
        ? (data as { error: string }).error
        : null;
      setScheduledLoadError(loadError);
    } catch (err) {
      if (activeDeviceRef.current !== deviceId) return;
      if (err instanceof DeviceUnreachableError) {
        // Same reasoning as fetchSessions above: this hook's own most
        // recent probe (whichever one just ran) decides deviceUnreachable.
        setScheduled([]);
        setHasLoadedScheduled(true);
        setDeviceUnreachable(true);
        setScheduledLoadError(null);
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
    deviceUnreachable, scheduledLoadError,
    reloadSessions: fetchSessions, reloadSchedules: fetchScheduled,
  };
}
