// Logs.tsx — device health view: fetches /rc/stats and renders load, OS, token history.
import { useState, useEffect } from 'react';
import { RT, FONT_MONO, fmtK } from '../tokens';
import { api, DeviceUnreachableError, ApiError } from '../api';
import type { DeviceCard } from '../types';

interface StatsData {
  os: string;
  loadavg: [number, number, number];
  cores: number;
  token_history: number[];
  tokens_now?: number;
  sessions?: number;
}

export interface LogsProps {
  device: DeviceCard;
}

export function Logs({ device }: LogsProps) {
  const [stats, setStats] = useState<StatsData | null>(null);
  // Before req() (api.ts) distinguished this, a confirmed-unreachable
  // device's 502 error body resolved as if it were a real StatsData
  // object: setStats(data) stored it, stats.loadavg was undefined, and
  // stats.loadavg[0] below threw synchronously during render, crashing
  // this component (see v2.1.16's root ErrorBoundary, main.tsx, for what
  // that would have taken down). Now the fetch below throws a
  // DeviceUnreachableError instead, which is caught and never reaches
  // setStats, so the crash can no longer happen; this flag exists so the
  // fix doesn't just trade the crash for a permanent "loading stats…"
  // (this device never polls again after mount, so with nothing else
  // distinguishing the two, a confirmed-unreachable device would hang on
  // that text forever instead of saying so).
  const [unreachable, setUnreachable] = useState(false);
  // Round 5: round 3's generator fix only ever threw DeviceUnreachableError
  // for the one "device unreachable" 502 shape; a non-502 error body (e.g.
  // {"error": "unknown device"}, 404, if this device were removed from the
  // registry) still resolved as ordinary data until round 5 restored that
  // coverage in the generator (api.ts's ApiError). Kept separate from
  // `unreachable` since it's a different, less common failure with its
  // own message rather than being folded into "device unreachable".
  const [loadError, setLoadError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setStats(null);
    setUnreachable(false);
    setLoadError(null);
    api.stats(device.id)
      .then((data: StatsData) => { if (!cancelled) setStats(data); })
      .catch((err) => {
        if (cancelled) return;
        if (err instanceof DeviceUnreachableError) setUnreachable(true);
        else if (err instanceof ApiError) setLoadError(err.message);
        // Any other failure: stay on "loading stats…" below, same as
        // before -- this view has no retry/poll of its own, so a generic
        // failure here isn't distinguishable from "hasn't answered yet".
      });
    return () => { cancelled = true; };
  }, [device.id]);

  const now = new Date().toLocaleTimeString('en-US', { hour12: false });

  let text: string;
  if (unreachable) {
    text = `[${now}] ${device.name} · device unreachable`;
  } else if (loadError) {
    text = `[${now}] ${device.name} · ${loadError}`;
  } else if (!stats) {
    text = `[${now}] ${device.name} · loading stats…`;
  } else {
    const load0 = stats.loadavg[0].toFixed(2);
    const cores = stats.cores;
    const loadPct = Math.round((stats.loadavg[0] / cores) * 100);
    const tokensNow = stats.tokens_now ?? device.tokens;
    // device.sessions is null for a device overview.py can't currently
    // reach; stats being truthy here only proves this direct /rc/stats
    // call succeeded, which can race ahead of a stale overview card still
    // reporting the device unreachable. Without the last fallback this
    // rendered the literal text "sessions null".
    const sessionCount = stats.sessions ?? device.sessions ?? '—';
    const historySamples = stats.token_history?.length ?? 0;

    text = [
      `[${now}] ${device.name} · ${stats.os}`,
      `load ${load0} / ${cores} cores  (${loadPct}%)`,
      `sessions ${sessionCount} · tokens ${fmtK(tokensNow)}`,
      `history ${historySamples} samples`,
    ].join('\n');
  }

  return (
    <pre style={{
      margin: 0,
      fontFamily: FONT_MONO,
      fontSize: 11,
      color: RT.textDim,
      lineHeight: 1.55,
      whiteSpace: 'pre-wrap',
    }}>
      {text}
    </pre>
  );
}
