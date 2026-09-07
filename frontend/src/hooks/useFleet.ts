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
  /** True while running on the 5s polling fallback instead of SSE. */
  usingFallback: boolean;
}

const POLL_INTERVAL_MS = 5000;
const EMPTY_VIEW: FleetView = { devices: [], sessions: [] };

export function useFleet(): UseFleetResult {
  const [view, setView] = useState<FleetView>(EMPTY_VIEW);
  const [connected, setConnected] = useState(false);
  const [usingFallback, setUsingFallback] = useState(false);
  const pollTimer = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    let cancelled = false;
    let es: EventSource | null = null;

    const stopPolling = () => {
      if (pollTimer.current) {
        clearInterval(pollTimer.current);
        pollTimer.current = null;
      }
    };

    const startPolling = () => {
      if (cancelled || pollTimer.current) return;
      setUsingFallback(true);
      const poll = async () => {
        try {
          const data = await fetchFleet();
          if (!cancelled) setView(data);
        } catch {
          // Keep the last known view; the next tick tries again.
        }
      };
      poll();
      pollTimer.current = setInterval(poll, POLL_INTERVAL_MS);
    };

    try {
      es = new EventSource('/rc/api/fleet/stream');
      es.onopen = () => {
        if (cancelled) return;
        setConnected(true);
        setUsingFallback(false);
        stopPolling();
      };
      es.onmessage = (ev) => {
        if (cancelled) return;
        try {
          setView(JSON.parse(ev.data) as FleetView);
        } catch {
          // Malformed frame — ignore, wait for the next one.
        }
      };
      es.onerror = () => {
        if (cancelled) return;
        setConnected(false);
        es?.close();
        startPolling();
      };
    } catch {
      startPolling();
    }

    return () => {
      cancelled = true;
      es?.close();
      stopPolling();
    };
  }, []);

  return { devices: view.devices, sessions: view.sessions, connected, usingFallback };
}
