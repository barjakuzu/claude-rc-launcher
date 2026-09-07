// useFleet.ts — subscribes to the hub-wide fleet roll-up (Task 8/9's
// /api/fleet + /api/fleet/stream) with automatic fallback to polling when
// SSE drops or isn't available. Replaces useAllSessions's per-device
// fan-out (useCrossDevice.ts) as the Sessions tab's data source.
import { useEffect, useRef, useState } from 'react';
import { fetchFleet } from '../api';
import type { FleetDevice, FleetSession, FleetView } from '../api';

export type { FleetDevice, FleetSession };

export interface UseFleetResult {
  devices: FleetDevice[];
  sessions: FleetSession[];
  /** True while the SSE stream is open and delivering updates. */
  connected: boolean;
  /** True while running the polling fallback (SSE down, reconnecting, or
   * gone stale — alongside SSE, not instead of it, once it recovers). */
  usingFallback: boolean;
  /** True when the stream is nominally open but hasn't produced a frame
   * in a while — the fleet view may be out of date. */
  stale: boolean;
}

const POLL_INTERVAL_MS = 5000;
// server.py's SSE_HEARTBEAT_SECONDS is 20s; every interval the server
// sends either a fleet snapshot or a {"type":"heartbeat"} data frame, so
// onmessage fires and lastFrameAt advances even on a quiet-but-healthy
// stream. ~2 intervals of slack before declaring the stream stale.
const STALE_MS = 40_000;
const RECONNECT_BASE_MS = 1000;
const RECONNECT_MAX_MS = 30_000;
const EMPTY_VIEW: FleetView = { devices: [], sessions: [] };

export function useFleet(): UseFleetResult {
  const [view, setView] = useState<FleetView>(EMPTY_VIEW);
  const [connected, setConnected] = useState(false);
  const [usingFallback, setUsingFallback] = useState(false);
  const [stale, setStale] = useState(false);
  const pollTimer = useRef<ReturnType<typeof setInterval> | null>(null);
  const staleWatchTimer = useRef<ReturnType<typeof setInterval> | null>(null);
  const reconnectTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    let cancelled = false;
    let es: EventSource | null = null;
    let lastFrameAt = 0;
    let backoffMs = RECONNECT_BASE_MS;

    const stopPolling = () => {
      if (pollTimer.current) {
        clearInterval(pollTimer.current);
        pollTimer.current = null;
      }
      setUsingFallback(false);
    };

    const poll = async () => {
      try {
        const data = await fetchFleet();
        if (!cancelled) setView(data);
      } catch {
        // Keep the last known view; the next tick tries again.
      }
    };

    const startPolling = () => {
      if (cancelled || pollTimer.current) return;
      setUsingFallback(true);
      poll();
      pollTimer.current = setInterval(poll, POLL_INTERVAL_MS);
    };

    const stopStaleWatch = () => {
      if (staleWatchTimer.current) {
        clearInterval(staleWatchTimer.current);
        staleWatchTimer.current = null;
      }
    };

    const startStaleWatch = () => {
      stopStaleWatch();
      staleWatchTimer.current = setInterval(() => {
        if (cancelled) return;
        const isStale = Date.now() - lastFrameAt > STALE_MS;
        setStale(isStale);
        if (isStale) startPolling();
      }, 5000);
    };

    const clearReconnectTimer = () => {
      if (reconnectTimer.current) {
        clearTimeout(reconnectTimer.current);
        reconnectTimer.current = null;
      }
    };

    const scheduleReconnect = () => {
      if (cancelled) return;
      clearReconnectTimer();
      reconnectTimer.current = setTimeout(() => {
        reconnectTimer.current = null;
        connect();
      }, backoffMs);
      backoffMs = Math.min(backoffMs * 2, RECONNECT_MAX_MS);
    };

    function connect() {
      if (cancelled) return;
      try {
        es = new EventSource('/rc/api/fleet/stream');
      } catch {
        startPolling();
        scheduleReconnect();
        return;
      }
      es.onopen = () => {
        if (cancelled) return;
        backoffMs = RECONNECT_BASE_MS;
        lastFrameAt = Date.now();
        setConnected(true);
        setStale(false);
        stopPolling();
        startStaleWatch();
      };
      es.onmessage = (ev) => {
        if (cancelled) return;
        lastFrameAt = Date.now();
        setStale(false);
        try {
          const parsed = JSON.parse(ev.data) as FleetView | { type: 'heartbeat'; ts: number };
          // A heartbeat frame only proves the stream is alive — it
          // carries no fleet data, so it must not overwrite the view.
          if (!('type' in parsed && parsed.type === 'heartbeat')) {
            setView(parsed as FleetView);
          }
        } catch {
          // Malformed frame — ignore, wait for the next one.
        }
        // A fresh frame means the stream is healthy again; drop the
        // stale-triggered backup poll if one was running.
        if (pollTimer.current) stopPolling();
      };
      es.onerror = () => {
        if (cancelled) return;
        setConnected(false);
        es?.close();
        es = null;
        stopStaleWatch();
        startPolling();
        scheduleReconnect();
      };
    }

    // Eager first fetch so the tab isn't empty until the first SSE frame
    // (or forever, if SSE never connects).
    poll();
    connect();

    return () => {
      cancelled = true;
      es?.close();
      es = null;
      stopPolling();
      stopStaleWatch();
      clearReconnectTimer();
    };
  }, []);

  return { devices: view.devices, sessions: view.sessions, connected, usingFallback, stale };
}
