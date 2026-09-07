// useAlerts.ts: polls the hub-wide GET /api/alerts (Phase 3 wiring,
// CONTRACT.md sections 3/5) for the header's alerts indicator. Plain
// interval polling, not SSE (the contract defines no streaming route for
// this endpoint, unlike /api/fleet/stream, see useFleet.ts).
//
// The route does not exist on the backend yet (a parallel lane is building
// it), so the very first failure is reported as 'unavailable' rather than
// 'loading' forever. Once a fetch has ever succeeded, later transient
// failures keep the last known report instead of flapping the header badge.
import { useEffect, useRef, useState } from 'react';
import { fetchAlerts } from '../api';
import type { AlertsReport } from '../api';

const POLL_INTERVAL_MS = 15_000;

export type AlertsStatus = 'loading' | 'ok' | 'unavailable';

export interface UseAlertsResult {
  report: AlertsReport | null;
  status: AlertsStatus;
}

export function useAlerts(): UseAlertsResult {
  const [report, setReport] = useState<AlertsReport | null>(null);
  const [status, setStatus] = useState<AlertsStatus>('loading');
  const everOk = useRef(false);

  useEffect(() => {
    let cancelled = false;

    const poll = async () => {
      try {
        const data = await fetchAlerts();
        if (cancelled) return;
        everOk.current = true;
        setReport(data);
        setStatus('ok');
      } catch {
        if (cancelled) return;
        if (!everOk.current) setStatus('unavailable');
        // Otherwise keep the last known report and status: a single
        // missed poll should not blank out a working indicator.
      }
    };

    poll();
    const t = setInterval(poll, POLL_INTERVAL_MS);
    return () => { cancelled = true; clearInterval(t); };
  }, []);

  return { report, status };
}
