// SessionRow.tsx — RSessionRow ported from variant-ops-refined.jsx lines 553-583.
import type { CSSProperties } from 'react';
import { RT, FONT_MONO, tintFor } from '../tokens';
import { Icons, CapBar, StatusPill } from './primitives';
import { btn } from './btn';
import type { Session } from '../types';
import { api } from '../api';

export interface SessionRowProps {
  s: Session;
  hue: number;
  deviceId: string;
  onChanged: () => void;
}

function truncate(s: string, n: number): string {
  return s.length > n ? s.slice(0, n) + '…' : s;
}

export function SessionRow({ s, hue, deviceId, onChanged }: SessionRowProps) {
  const hueColor = tintFor(hue, 0.66, 0.08);

  // Compute dir: basename of workdir.
  const dir = s.workdir
    ? (s.workdir.replace(/\/$/, '').split('/').pop() || s.workdir)
    : '—';

  // Compute pct.
  const pct = s.pct !== undefined
    ? s.pct
    : Math.min(100, Math.round(((s.tokens || 0) / 2000) * 100));

  // Compute tokens label.
  const tokensLabel = `${Math.round((s.tokens || 0) / 1000)}K`;

  // Compute sessionId display string.
  let sessionIdDisplay = '—';
  if (s.sessionId) {
    sessionIdDisplay = truncate(s.sessionId, 28);
  } else if (s.url) {
    const tail = s.url.replace(/.*\//, '');
    sessionIdDisplay = truncate(tail, 28);
  }

  // Clipboard copy: prefer sessionId, fallback url.
  const copyValue = s.sessionId || s.url || '';

  const handleCopy = () => {
    if (copyValue) {
      navigator.clipboard.writeText(copyValue).catch(() => {/* ignore */});
    }
  };

  const handleLink = () => {
    if (s.url) window.open(s.url, '_blank');
  };

  const handleRefresh = async () => {
    try { await api.restart(deviceId, s.name); } catch {/* ignore */}
    onChanged();
  };

  const handleStop = async () => {
    try { await api.stop(deviceId, s.name); } catch {/* ignore */}
    onChanged();
  };

  const miniStyle: CSSProperties = btn('mini');

  return (
    <div style={{
      background: RT.card,
      border: `1px solid ${RT.border}`,
      borderRadius: 8,
      padding: '10px 12px',
      display: 'flex',
      flexDirection: 'column',
      gap: 6,
    }}>
      {/* Title row */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <div style={{
          fontSize: 12,
          fontWeight: 600,
          flex: 1,
          minWidth: 0,
          whiteSpace: 'nowrap',
          overflow: 'hidden',
          textOverflow: 'ellipsis',
        }}>
          {s.name}
        </div>
        <StatusPill status={s.status || 'idle'} />
      </div>

      {/* Dir + CapBar + tokens */}
      <div style={{
        display: 'flex',
        alignItems: 'center',
        gap: 8,
        fontSize: 10,
        fontFamily: FONT_MONO,
        color: RT.textDim,
      }}>
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 3, whiteSpace: 'nowrap' }}>
          <Icons.folder size={9} stroke={RT.textLow} /> {dir}
        </span>
        <CapBar pct={pct} height={2} bg="rgba(255,255,255,.04)" color={hueColor} />
        <span style={{ whiteSpace: 'nowrap', color: RT.textDim }}>{tokensLabel}</span>
      </div>

      {/* SessionId + action buttons */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
        <div style={{
          flex: 1,
          fontFamily: FONT_MONO,
          fontSize: 10,
          color: RT.textLow,
          whiteSpace: 'nowrap',
          overflow: 'hidden',
          textOverflow: 'ellipsis',
        }}>
          {sessionIdDisplay}
        </div>
        <button style={miniStyle} title="Copy session ID" onClick={handleCopy}>
          <Icons.copy size={10} stroke={RT.textDim} />
        </button>
        <button
          style={{ ...miniStyle, opacity: s.url ? 1 : 0.4, cursor: s.url ? 'pointer' : 'default' }}
          title="Open session URL"
          onClick={handleLink}
        >
          <Icons.link size={10} stroke={RT.textDim} />
        </button>
        <button style={miniStyle} title="Restart session" onClick={handleRefresh}>
          <Icons.refresh size={10} stroke={RT.green} />
        </button>
        <button style={miniStyle} title="Stop session" onClick={handleStop}>
          <Icons.stop size={9} stroke={RT.red} />
        </button>
        {/* ⋯ menu stub — wired in Task 12 */}
        <button style={miniStyle} title="More options" onClick={() => {/* Task 12 */}}>
          <Icons.more size={10} stroke={RT.textDim} />
        </button>
      </div>
    </div>
  );
}
