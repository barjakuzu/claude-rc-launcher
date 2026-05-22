// Logs.tsx — device health view: fetches /rc/stats and renders load, OS, token history.
import { useState, useEffect } from 'react';
import { RT, FONT_MONO, fmtK } from '../tokens';
import { api } from '../api';
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

  useEffect(() => {
    let cancelled = false;
    api.stats(device.id)
      .then((data: StatsData) => { if (!cancelled) setStats(data); })
      .catch(() => {/* ignore */});
    return () => { cancelled = true; };
  }, [device.id]);

  const now = new Date().toLocaleTimeString('en-US', { hour12: false });

  let text: string;
  if (!stats) {
    text = `[${now}] ${device.name} · loading stats…`;
  } else {
    const load0 = stats.loadavg[0].toFixed(2);
    const cores = stats.cores;
    const loadPct = Math.round((stats.loadavg[0] / cores) * 100);
    const tokensNow = stats.tokens_now ?? device.tokens;
    const sessionCount = stats.sessions ?? device.sessions;
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
