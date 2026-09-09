// usePanelData.ts — shared data-fetching hook for device detail views.
import { useState, useEffect, useCallback, useRef } from 'react';
import { api, DeviceUnreachableError, isFailureEnvelope } from './api';
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
  // Round 4: confirmed by this hook's own direct /rc/sessions probe
  // (polls every 4s), which is fresher and more authoritative for "is
  // this specific device reachable" than the separate /rc/overview poll
  // (every 5s) DeviceDetail.tsx used to defer to for its "Device
  // offline." message. Reset to false by a successful sessions fetch, or
  // by a device switch.
  //
  // Round 6: round 5 consolidated this with an equivalent schedules-side
  // flag into one shared `deviceUnreachable`, written by BOTH
  // fetchSessions and fetchScheduled. That reopened the exact bug this
  // whole lane keeps finding, in a new shape: with /sessions answering
  // 502 (unreachable) and /schedules answering 200 (fine) moments later,
  // fetchScheduled's own success unconditionally cleared the shared flag,
  // so the Sessions tab lost fetchSessions' own, still-correct knowledge
  // that the device is unreachable and fell through to "No active
  // sessions on X. Launch one above." -- one fetch's success erasing a
  // DIFFERENT fetch's answer. This is the second time in this lane that
  // consolidating two signals into one has done this (the first was
  // deleting isErrorResponse in round 3). These two are per-fetch, not
  // per-device, on purpose: kept separate so success on one can never
  // clear what the other one knows.
  const [sessionsUnreachable, setSessionsUnreachable] = useState(false);
  const [scheduledUnreachable, setScheduledUnreachable] = useState(false);
  // Round 6: a reachable device can still answer /sessions or /schedules
  // with a real, non-list response: {"ok": false, "message": "..."} , most
  // commonly a metadata-role device's blanket 403 gate (server.py checks
  // this against the whole path, not per-endpoint) -- "the work MacBook
  // Pro" in this fleet runs this role, so this is a real, reachable shape,
  // not a hypothetical one. Neither "unreachable" (the device answered)
  // nor "confirmed zero" (nothing resembling a session/schedule list was
  // ever returned): its own message, kept separate per fetch for the same
  // reason sessionsUnreachable/scheduledUnreachable are.
  const [sessionsError, setSessionsError] = useState<string | null>(null);
  const [scheduledError, setScheduledError] = useState<string | null>(null);
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
    setSessionsUnreachable(false);
    setScheduledUnreachable(false);
    setSessionsError(null);
    setScheduledError(null);
    setScheduledLoadError(null);
  }, [deviceId]);

  const fetchSessions = useCallback(async () => {
    try {
      const data = await api.sessions(deviceId);
      if (activeDeviceRef.current !== deviceId) return; // stale: device switched mid-flight
      if (isFailureEnvelope(data)) {
        // A real, reachable response that is not a session list either
        // (see sessionsError above): treat as a definitive but distinct
        // answer, not as zero sessions.
        setSessions([]);
        setHasLoadedSessions(true);
        setSessionsUnreachable(false);
        setSessionsError(data.message || 'This device refused the request.');
        return;
      }
      const arr: Session[] = Array.isArray(data) ? data : (data?.sessions ?? []);
      setSessions(arr);
      setHasLoadedSessions(true);
      setSessionsUnreachable(false);
      setSessionsError(null);
    } catch (err) {
      if (activeDeviceRef.current !== deviceId) return;
      if (err instanceof DeviceUnreachableError) {
        // A confirmed "the hub cannot reach this device" IS an answer,
        // distinct from "we haven't heard back yet" -- unlike a generic
        // network failure (below), this must flip hasLoaded so
        // DeviceDetail.tsx's offline branch becomes reachable instead of
        // hanging on "Loading sessions…" forever, and sessionsUnreachable
        // so that branch renders "Device offline." from this fetch's own
        // fresher answer rather than deferring to a staler one.
        setSessions([]);
        setHasLoadedSessions(true);
        setSessionsUnreachable(true);
        setSessionsError(null);
      }
      // Any other failure: keep whatever was last known, don't claim
      // we've loaded and don't claim we've confirmed unreachable either.
    }
  }, [deviceId]);

  const fetchScheduled = useCallback(async () => {
    try {
      const data = await api.schedules(deviceId);
      if (activeDeviceRef.current !== deviceId) return;
      if (isFailureEnvelope(data)) {
        setScheduled([]);
        setHasLoadedScheduled(true);
        setScheduledUnreachable(false);
        setScheduledError(data.message || 'This device refused the request.');
        setScheduledLoadError(null);
        return;
      }
      const arr: Schedule[] = Array.isArray(data) ? data : (data?.schedules ?? []);
      setScheduled(arr);
      setHasLoadedScheduled(true);
      setScheduledUnreachable(false);
      setScheduledError(null);
      const loadError = (!Array.isArray(data) && data && typeof data === 'object'
        && typeof (data as { error?: unknown }).error === 'string')
        ? (data as { error: string }).error
        : null;
      setScheduledLoadError(loadError);
    } catch (err) {
      if (activeDeviceRef.current !== deviceId) return;
      if (err instanceof DeviceUnreachableError) {
        // Same reasoning as fetchSessions above: this hook's own most
        // recent probe decides ITS OWN unreachable flag, never the other
        // fetch's.
        setScheduled([]);
        setHasLoadedScheduled(true);
        setScheduledUnreachable(true);
        setScheduledError(null);
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
    sessionsUnreachable, scheduledUnreachable,
    sessionsError, scheduledError, scheduledLoadError,
    reloadSessions: fetchSessions, reloadSchedules: fetchScheduled,
  };
}
