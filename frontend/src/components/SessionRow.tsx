// SessionRow.tsx — V5 full-width 3-col grid with 34×34 V5IconButton actions.
import { useState, useEffect, useRef } from 'react';
import { RT, FONT_MONO, tintFor, Z } from '../tokens';
import { Icons, CapBar, Dot } from './primitives';
import { V5IconButton } from './V5IconButton';
import { fixedMenuPos } from './menuPos';
import type { Session } from '../types';
import { api } from '../api';

export interface SessionRowProps {
  s: Session;
  hue: number;
  deviceId: string;
  mobile?: boolean;
  onChanged: () => void;
  onPreview: (name: string) => void;
}

function truncate(s: string, n: number): string {
  return s.length > n ? s.slice(0, n) + '…' : s;
}

// V5StatusPill: bordered colored pill with dot
function V5StatusPill({ status }: { status: string }) {
  const map: Record<string, { label: string; color: string; pulse: boolean }> = {
    running:  { label: 'running',  color: RT.green,   pulse: true  },
    thinking: { label: 'thinking', color: RT.amber,   pulse: true  },
    idle:     { label: 'idle',     color: RT.textLow, pulse: false },
    stopped:  { label: 'stopped',  color: RT.red,     pulse: false },
  };
  const m = map[status] || { label: status, color: RT.textLow, pulse: false };
  return (
    <span style={{
      display: 'inline-flex', alignItems: 'center', gap: 5,
      fontSize: 9.5, letterSpacing: '.08em', textTransform: 'uppercase',
      fontFamily: FONT_MONO, padding: '2px 7px', borderRadius: 4,
      border: `1px solid ${m.color === RT.textLow ? RT.border : m.color}`,
      color: m.color, opacity: m.color === RT.textLow ? 0.7 : 1,
      flex: 'none',
    }}>
      <Dot color={m.color} size={5} pulse={m.pulse} />
      {m.label}
    </span>
  );
}

export function SessionRow({ s, hue, deviceId, mobile = false, onChanged, onPreview }: SessionRowProps) {
  const [pending, setPending] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const [menuPos, setMenuPos] = useState<React.CSSProperties | null>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const hueColor = tintFor(hue, 0.70, 0.10);

  // Click-outside to close ⋯ menu
  useEffect(() => {
    const off = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) setMenuOpen(false);
    };
    if (menuOpen) document.addEventListener('mousedown', off);
    return () => document.removeEventListener('mousedown', off);
  }, [menuOpen]);

  // dir: basename of workdir
  const dir = s.workdir
    ? (s.workdir.replace(/\/$/, '').split('/').pop() || s.workdir)
    : '—';

  // pct
  const pct = s.pct !== undefined
    ? s.pct
    : Math.min(100, Math.round(((s.tokens || 0) / 200000) * 100));

  // tokens label
  const tokensLabel = `${Math.round((s.tokens || 0) / 1000)}K`;

  // sessionId display
  let sessionIdDisplay = '—';
  if (s.sessionId) {
    sessionIdDisplay = truncate(s.sessionId, 30);
  } else if (s.url) {
    const tail = s.url.replace(/.*\//, '');
    sessionIdDisplay = truncate(tail, 30);
  }

  const copyValue = s.sessionId || s.url || '';

  const handleCopy = () => {
    if (copyValue) navigator.clipboard.writeText(copyValue).catch(() => {/* ignore */});
  };

  const handleLink = () => {
    if (s.url) window.open(s.url, '_blank');
  };

  const handleRefresh = async () => {
    setPending(true);
    try { await api.restart(deviceId, s.name); } catch {/* ignore */}
    finally { setPending(false); onChanged(); }
  };

  const handleStop = async () => {
    setPending(true);
    try { await api.stop(deviceId, s.name); } catch {/* ignore */}
    finally { setPending(false); onChanged(); }
  };

  const handleUnstick = async () => {
    setMenuOpen(false);
    setPending(true);
    try { await api.unstick(deviceId, s.name); } catch {/* ignore */}
    finally { setPending(false); onChanged(); }
  };

  const handlePreviewClick = () => {
    setMenuOpen(false);
    onPreview(s.name);
  };

  const menuItemStyle: React.CSSProperties = {
    width: '100%', textAlign: 'left', background: 'transparent',
    border: 'none', borderRadius: 4, padding: '7px 9px', cursor: 'pointer',
    color: RT.text, fontFamily: 'inherit', fontSize: 12,
    display: 'flex', alignItems: 'center', gap: 7, whiteSpace: 'nowrap',
  };

  return (
    <div style={{
      background: RT.card, border: `1px solid ${RT.border}`,
      borderRadius: 10, padding: mobile ? 14 : '14px 18px',
      display: 'grid',
      gridTemplateColumns: mobile ? '1fr' : 'minmax(220px, 1.4fr) minmax(180px, 1fr) auto',
      gap: mobile ? 12 : 18, alignItems: 'center',
    }}>
      {/* Col 1: Name + dir + sessionId */}
      <div style={{ minWidth: 0 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 5 }}>
          <div style={{
            fontSize: 14, fontWeight: 600, letterSpacing: '-.005em',
            whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', flex: 1, minWidth: 0,
          }}>
            {s.name}
          </div>
          <V5StatusPill status={s.status || 'idle'} />
        </div>
        <div style={{
          display: 'flex', alignItems: 'center', gap: 8,
          fontFamily: FONT_MONO, fontSize: 11, color: RT.textLow, flexWrap: 'wrap',
        }}>
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
            <Icons.folder size={10} stroke={RT.textLow} /> {dir}
          </span>
          <span style={{ color: RT.borderHi }}>·</span>
          <span>{s.mode || 'STANDARD'}</span>
          <span style={{ color: RT.borderHi }}>·</span>
          <span style={{ whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', maxWidth: 200 }}>
            {sessionIdDisplay}
          </span>
        </div>
      </div>

      {/* Col 2: Tokens + bar */}
      <div style={{ minWidth: 0 }}>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 6, marginBottom: 5 }}>
          <span style={{ fontFamily: FONT_MONO, fontSize: 14, fontWeight: 500 }}>{tokensLabel}</span>
          <span style={{ fontFamily: FONT_MONO, fontSize: 11, color: RT.textLow }}>tokens · {pct}%</span>
        </div>
        <CapBar pct={pct} height={4} bg="rgba(255,255,255,.04)" color={hueColor} />
      </div>

      {/* Col 3: Actions — consolidated to 3 buttons (Restart / Stop / More) */}
      <div style={{ display: 'flex', gap: 6, justifyContent: mobile ? 'flex-end' : 'flex-end' }}>
        <V5IconButton
          label="Restart session"
          accent={RT.green}
          mobile={mobile}
          pending={pending}
          onClick={handleRefresh}
        >
          <Icons.refresh size={14} />
        </V5IconButton>
        <V5IconButton
          label="Stop session"
          accent={RT.red}
          mobile={mobile}
          pending={pending}
          onClick={handleStop}
        >
          <Icons.stop size={12} />
        </V5IconButton>

        {/* ⋯ more menu — secondary actions */}
        <div ref={menuRef} style={{ position: 'relative' }}>
          <V5IconButton
            label="More options"
            mobile={mobile}
            pending={pending}
            onClick={() => {
              if (!menuOpen && menuRef.current) setMenuPos(fixedMenuPos(menuRef.current));
              setMenuOpen((o) => !o);
            }}
          >
            <Icons.more size={14} stroke={RT.textDim} />
          </V5IconButton>

          {menuOpen && (
            <div style={{
              ...(menuPos ?? {}),
              background: RT.panel, border: `1px solid ${RT.borderHi}`,
              borderRadius: 8, padding: 4, zIndex: Z.menu,
              boxShadow: '0 8px 24px rgba(0,0,0,.4)', minWidth: 160,
            }}>
              <button
                style={menuItemStyle} onClick={() => { setMenuOpen(false); handleCopy(); }}
                onMouseEnter={(e) => { (e.currentTarget as HTMLButtonElement).style.background = RT.bgRaised; }}
                onMouseLeave={(e) => { (e.currentTarget as HTMLButtonElement).style.background = 'transparent'; }}
              >
                <Icons.copy size={11} stroke={RT.textDim} /> Copy session ID
              </button>
              <button
                style={{ ...menuItemStyle, opacity: s.url ? 1 : 0.45, cursor: s.url ? 'pointer' : 'default' }}
                onClick={() => { if (s.url) { setMenuOpen(false); handleLink(); } }}
                onMouseEnter={(e) => { if (s.url) (e.currentTarget as HTMLButtonElement).style.background = RT.bgRaised; }}
                onMouseLeave={(e) => { (e.currentTarget as HTMLButtonElement).style.background = 'transparent'; }}
              >
                <Icons.link size={11} stroke={RT.textDim} /> Open URL
              </button>
              <button
                style={menuItemStyle} onClick={handlePreviewClick}
                onMouseEnter={(e) => { (e.currentTarget as HTMLButtonElement).style.background = RT.bgRaised; }}
                onMouseLeave={(e) => { (e.currentTarget as HTMLButtonElement).style.background = 'transparent'; }}
              >
                <Icons.search size={11} stroke={RT.textDim} /> Preview
              </button>
              <button
                style={menuItemStyle} onClick={handleUnstick}
                onMouseEnter={(e) => { (e.currentTarget as HTMLButtonElement).style.background = RT.bgRaised; }}
                onMouseLeave={(e) => { (e.currentTarget as HTMLButtonElement).style.background = 'transparent'; }}
              >
                <Icons.refresh size={11} stroke={RT.amber} /> Unstick
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
