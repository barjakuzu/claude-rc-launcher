// useCost.ts: polls GET /api/cost (Phase 3 wiring, CONTRACT.md sections
// 3/5) for the Cost view. Same shape as useAlerts.ts: a steady interval
// while healthy, exponential backoff on failure (pollWithBackoff.ts) so a
// permanently-404ing route (the endpoint doesn't exist yet; a parallel
// lane is building it) doesn't get hammered every 30s forever.
import { useEffect, useRef, useState } from 'react';
import { fetchCost } from '../api';
import type { CostReport } from '../api';
import { pollWithBackoff } from './pollWithBackoff';

const POLL_INTERVAL_MS = 30_000;
const DEFAULT_DAYS = 30;

export type CostStatus = 'loading' | 'ok' | 'unavailable';

export interface UseCostResult {
  report: CostReport | null;
  status: CostStatus;
}

export function useCost(days = DEFAULT_DAYS): UseCostResult {
  const [report, setReport] = useState<CostReport | null>(null);
  const [status, setStatus] = useState<CostStatus>('loading');
  const everOk = useRef(false);

  useEffect(() => {
    const stop = pollWithBackoff(async () => {
      try {
        const data = await fetchCost(days);
        everOk.current = true;
        setReport(data);
        setStatus('ok');
      } catch (err) {
        if (!everOk.current) setStatus('unavailable');
        // Otherwise keep the last known report: a single missed poll
        // should not blank a working view out from under the user.
        throw err; // tells pollWithBackoff to back off before retrying.
      }
    }, { intervalMs: POLL_INTERVAL_MS });

    return stop;
  }, [days]);

  return { report, status };
}
