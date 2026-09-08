// useLimits.ts: polls GET /api/limits (Phase L2 wiring, CONTRACT.md sections
// 3/5/6) for the header's limits indicator. Same shape as useCost.ts /
// useAlerts.ts: a steady interval while healthy, exponential backoff on
// failure (pollWithBackoff.ts) so a permanently-404ing route (the endpoint
// doesn't exist yet; a parallel lane is building it) doesn't get hammered
// every 30s forever.
import { useEffect, useRef, useState } from 'react';
import { fetchLimits } from '../api';
import type { LimitsReport } from '../api';
import { pollWithBackoff } from './pollWithBackoff';

const POLL_INTERVAL_MS = 30_000;

export type LimitsStatus = 'loading' | 'ok' | 'unavailable';

export interface UseLimitsResult {
  report: LimitsReport | null;
  status: LimitsStatus;
  /** Client clock (Date.now()) at the last successful fetch, null until
   * the first one lands. Round 4 (Critical 1): a dead endpoint used to
   * leave `report` (and the `age_seconds` baked into it) frozen forever
   * with `status` still 'ok', so a reading that was fresh at the last
   * successful poll kept looking exactly that fresh no matter how long
   * the endpoint had been down since. Consumers must derive the reading's
   * real current age as `deviceRow.age_seconds + (now - lastOkMs) / 1000`
   * rather than trusting age_seconds alone. */
  lastOkMs: number | null;
}

export function useLimits(): UseLimitsResult {
  const [report, setReport] = useState<LimitsReport | null>(null);
  const [status, setStatus] = useState<LimitsStatus>('loading');
  const [lastOkMs, setLastOkMs] = useState<number | null>(null);
  const everOk = useRef(false);

  useEffect(() => {
    const stop = pollWithBackoff(async () => {
      try {
        const data = await fetchLimits();
        everOk.current = true;
        setReport(data);
        setStatus('ok');
        setLastOkMs(Date.now());
      } catch (err) {
        if (!everOk.current) setStatus('unavailable');
        // Otherwise keep the last known report: a single missed poll
        // should not blank a working indicator out from under the user.
        // lastOkMs deliberately does NOT advance here: it marks the last
        // time we actually heard back, which is exactly the clock the
        // staleness math above needs to keep ticking correctly.
        throw err; // tells pollWithBackoff to back off before retrying.
      }
    }, { intervalMs: POLL_INTERVAL_MS });

    return stop;
  }, []);

  return { report, status, lastOkMs };
}
