// SessionRow.tsx — per-session card with actions (open, copy, restart, stop, unstick, preview).
import type { CSSProperties } from 'react';
import { useState, useEffect, useRef } from 'react';
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
  onPreview: (name: string) => void;
}

function truncate(s: string, n: number): string {
  return s.length > n ? s.slice(0, n) + '…' : s;
}

export function SessionRow({ s, hue, deviceId, onChanged, onPreview }: SessionRowProps) {
  const [pending, setPending] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);
  const hueColor = tintFor(hue, 0.66, 0.08);

  // Click-outside to close ⋯ menu
  useEffect(() => {
    const off = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) setMenuOpen(false);
    };
    if (menuOpen) document.addEventListener('mousedown', off);
    return () => document.removeEventListener('mousedown', off);
  }, [menuOpen]);

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
    setPending(true);
    try {
      await api.restart(deviceId, s.name);
    } catch {/* ignore */}
    finally {
      setPending(false);
      onChanged();
    }
  };

  const handleStop = async () => {
    setPending(true);
    try {
      await api.stop(deviceId, s.name);
    } catch {/* ignore */}
    finally {
      setPending(false);
      onChanged();
    }
  };

  const handleUnstick = async () => {
    setMenuOpen(false);
    setPending(true);
    try {
      await api.unstick(deviceId, s.name);
    } catch {/* ignore */}
    finally {
      setPending(false);
      onChanged();
    }
  };

  const handlePreviewClick = () => {
    setMenuOpen(false);
    onPreview(s.name);
  };

  const miniStyle: CSSProperties = btn('mini');

  const menuItemStyle: CSSProperties = {
    width: '100%',
    textAlign: 'left',
    background: 'transparent',
    border: 'none',
    borderRadius: 4,
    padding: '7px 9px',
    cursor: 'pointer',
    color: RT.text,
    fontFamily: 'inherit',
    fontSize: 11,
    display: 'flex',
    alignItems: 'center',
    gap: 7,
    whiteSpace: 'nowrap',
  };

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
        <button
          disabled={pending}
          style={{ ...miniStyle, opacity: pending ? 0.6 : 1 }}
          title="Restart session"
          onClick={handleRefresh}
        >
          <Icons.refresh size={10} stroke={RT.green} />
        </button>
        <button
          disabled={pending}
          style={{ ...miniStyle, opacity: pending ? 0.6 : 1 }}
          title="Stop session"
          onClick={handleStop}
        >
          <Icons.stop size={9} stroke={RT.red} />
        </button>

        {/* ⋯ menu */}
        <div ref={menuRef} style={{ position: 'relative' }}>
          <button
            style={{ ...miniStyle, opacity: pending ? 0.6 : 1 }}
            title="More options"
            disabled={pending}
            onClick={() => setMenuOpen((o) => !o)}
          >
            <Icons.more size={10} stroke={RT.textDim} />
          </button>

          {menuOpen && (
            <div style={{
              position: 'absolute',
              bottom: '100%',
              right: 0,
              marginBottom: 4,
              background: RT.panel,
              border: `1px solid ${RT.borderHi}`,
              borderRadius: 8,
              padding: 4,
              zIndex: 20,
              boxShadow: '0 8px 24px rgba(0,0,0,.4)',
              minWidth: 130,
            }}>
              <button
                style={menuItemStyle}
                onClick={handlePreviewClick}
                onMouseEnter={(e) => { (e.currentTarget as HTMLButtonElement).style.background = RT.bgRaised; }}
                onMouseLeave={(e) => { (e.currentTarget as HTMLButtonElement).style.background = 'transparent'; }}
              >
                <Icons.search size={10} stroke={RT.textDim} />
                Preview
              </button>
              <button
                style={menuItemStyle}
                onClick={handleUnstick}
                onMouseEnter={(e) => { (e.currentTarget as HTMLButtonElement).style.background = RT.bgRaised; }}
                onMouseLeave={(e) => { (e.currentTarget as HTMLButtonElement).style.background = 'transparent'; }}
              >
                <Icons.refresh size={10} stroke={RT.amber} />
                Unstick
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
