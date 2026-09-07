// AllSessions.tsx — Sessions tab, rendered from the hub-wide fleet store
// (useFleet, Task 12). Sessions on a currently-offline device still render
// here (dimmed, with a "last seen" age) instead of being silently dropped.
//
// external/pid/tmux/rc_url/tokens/mode are optional on FleetSession — a
// backend change lands them through the store into /api/fleet shaped like
// the per-device GET /sessions rows (sessions.py's list_rc_sessions()).
// Terminal/RC/Stop affordances below gate on those fields' *presence*, not
// on `isExternal` alone, and degrade to read-only when they're absent —
// mirrors SessionRow.tsx's semantics so both tabs behave identically.
import { useEffect, useMemo, useRef, useState } from 'react';
import { RT, FONT_MONO, tintFor, hueForId, Z, fmtUsage } from '../tokens';
import { Icons, Dot, StatusPill, ExternalBadge } from './primitives';
import { MobileHeader } from './MobileHeader';
import { mobileActionBtn } from './mobileActionBtn';
import { useFleet } from '../hooks/useFleet';
import type { FleetDevice, FleetSession } from '../hooks/useFleet';
import { api } from '../api';
import { formatRelativeTime } from '../relativeTime';
import { PreviewModal } from './PreviewModal';
import { fixedMenuPos } from './menuPos';

interface AllSessionsProps {
  onOpenDevice: (id: string) => void;
}

interface PreviewState { deviceId: string; name: string; sessionId?: string; mode?: string; }

export function AllSessions({ onOpenDevice }: AllSessionsProps) {
  const { devices, sessions, usingFallback, stale } = useFleet();
  const [pending, setPending] = useState<Record<string, boolean>>({});
  const [preview, setPreview] = useState<PreviewState | null>(null);
  const [rcUrls, setRcUrls] = useState<Record<string, string>>({});
  const [rcErrors, setRcErrors] = useState<Record<string, string>>({});

  const guard = async (key: string, fn: () => Promise<unknown>) => {
    if (pending[key]) return;
    setPending((p) => ({ ...p, [key]: true }));
    try { await fn(); } finally {
      setPending((p) => ({ ...p, [key]: false }));
    }
  };

  const handleEnableRc = async (key: string, deviceId: string, tmuxSessionName: string) => {
    if (pending[`rc-${key}`]) return;
    setPending((p) => ({ ...p, [`rc-${key}`]: true }));
    setRcErrors((e) => { const { [key]: _drop, ...rest } = e; return rest; });
    try {
      const result = await api.enableRc(deviceId, tmuxSessionName);
      if (result.ok && result.url) {
        setRcUrls((u) => ({ ...u, [key]: result.url as string }));
      } else {
        setRcErrors((e) => ({ ...e, [key]: result.message || 'Failed to enable Remote Control' }));
      }
    } catch (err) {
      setRcErrors((e) => ({ ...e, [key]: err instanceof Error ? err.message : 'Failed to enable Remote Control' }));
    } finally {
      setPending((p) => ({ ...p, [`rc-${key}`]: false }));
    }
  };

  // Auto-clear an rc error a few seconds after it lands.
  useEffect(() => {
    const keys = Object.keys(rcErrors);
    if (keys.length === 0) return;
    const t = setTimeout(() => setRcErrors({}), 6000);
    return () => clearTimeout(t);
  }, [rcErrors]);

  // The server's rc_url always wins when present. If a later poll/SSE frame
  // reports s.rc_url as null for a row we optimistically stored locally (RC
  // was disabled/reset server-side), drop the stale local value too.
  useEffect(() => {
    setRcUrls((u) => {
      let changed = false;
      const next = { ...u };
      for (const s of sessions) {
        const isAdopted = !!(s.kind === 'external' || s.external) && !!s.tmux;
        if (!isAdopted) continue;
        const key = `${s.device_id}:${s.session_id}`;
        if (s.rc_url === null && key in next) {
          delete next[key];
          changed = true;
        }
      }
      return changed ? next : u;
    });
  }, [sessions]);

  const deviceById = useMemo(
    () => new Map<string, FleetDevice>(devices.map((d) => [d.id, d])),
    [devices],
  );

  // needs_attention sorts first; tie-break on a stable key (started_at,
  // then session_id) rather than last_seen, which reorders rows on every
  // incoming event.
  const sortedSessions = useMemo(() => [...sessions].sort((a, b) => {
    if (a.needs_attention !== b.needs_attention) return a.needs_attention ? -1 : 1;
    const started = (b.started_at ?? 0) - (a.started_at ?? 0);
    if (started !== 0) return started;
    return a.session_id < b.session_id ? -1 : a.session_id > b.session_id ? 1 : 0;
  }), [sessions]);

  const deviceCount = new Set(sessions.map((s) => s.device_id)).size;
  const connectionNote = stale ? ' · stream stale, polling' : usingFallback ? ' · polling' : '';

  return (
    <div style={{ flex: 1, overflow: 'auto', display: 'flex', flexDirection: 'column' }}>
      <MobileHeader
        subtitle={`${sessions.length} total session${sessions.length !== 1 ? 's' : ''} · across ${deviceCount} device${deviceCount !== 1 ? 's' : ''}${connectionNote}`}
        title="Sessions"
        right={
          <button style={{ background: RT.panel, border: `1px solid ${RT.border}`, borderRadius: 7, width: 34, height: 34, display: 'flex', alignItems: 'center', justifyContent: 'center', cursor: 'pointer' }}>
            <Icons.filter size={14} stroke={RT.textDim} />
          </button>
        }
      />
      <div style={{ padding: '12px 12px 32px', display: 'flex', flexDirection: 'column', gap: 8 }}>
        {sortedSessions.length === 0 && (
          <div style={{ padding: 32, textAlign: 'center', color: RT.textLow, fontFamily: FONT_MONO, fontSize: 13, border: `1px dashed ${RT.border}`, borderRadius: 10 }}>
            No active sessions across devices.
          </div>
        )}
        {sortedSessions.map((s: FleetSession) => {
          const d = deviceById.get(s.device_id);
          const offline = d ? d.online === 0 : false;
          const hue = hueForId(s.device_id);
          const chipColor = tintFor(hue, 0.70, 0.10);
          const isExternal = s.kind === 'external' || !!s.external;
          const name = s.name ?? s.session_id;
          const key = `${s.device_id}:${s.session_id}`;

          // Mirrors SessionRow.tsx: an external row can only open a
          // terminal (Preview/keys/resize) when a tmux pane was found for
          // it. previewTarget is what api.preview/ws/keys/resize must
          // address — the launcher's own name for a launcher row, or the
          // adopted tmux session's real name for an external row.
          const isAdopted = isExternal && !!s.tmux;
          const canOpenTerminal = !isExternal || isAdopted;
          const previewTarget = isAdopted ? s.tmux!.session_name : name;
          const rowRcUrl = s.rc_url ?? rcUrls[key];
          const canStopExternal = isExternal && s.pid != null;
          // Effective (cumulative, cost-relevant) tokens: a different
          // metric from s.tokens (live context-window fill below).
          // null !== undefined here: null means the backend confirmed no
          // transcript data exists and must show as a dash, never a fake 0;
          // undefined means the field isn't sent yet and shows nothing.
          const usageLabel = fmtUsage(s.usage);

          const openPreview = () => setPreview({ deviceId: s.device_id, name: previewTarget, sessionId: s.session_id, mode: s.mode });

          return (
            <div
              key={key}
              onClick={canOpenTerminal ? openPreview : undefined}
              title={canOpenTerminal ? 'Open terminal' : undefined}
              style={{
                background: RT.card, border: `1px solid ${s.needs_attention ? RT.red : RT.border}`,
                borderRadius: 10, padding: 12,
                display: 'flex', flexDirection: 'column', gap: 8,
                cursor: canOpenTerminal ? 'pointer' : 'default',
                opacity: offline ? 0.6 : 1,
              }}>
              {/* Name + status */}
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <div style={{ flex: 1, fontSize: 14, fontWeight: 600, letterSpacing: '-.005em', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{name}</div>
                {isExternal && <ExternalBadge />}
                {isExternal && !isAdopted && (
                  <span style={{ fontFamily: FONT_MONO, fontSize: 10.5, color: RT.textLow, fontStyle: 'italic' }}>
                    not in tmux
                  </span>
                )}
                <StatusPill status={s.state ?? 'idle'} />
              </div>
              {/* Device chip */}
              <button onClick={(e) => { e.stopPropagation(); onOpenDevice(s.device_id); }} style={{
                background: 'transparent', border: 'none', cursor: 'pointer', padding: 0,
                display: 'inline-flex', alignItems: 'center', gap: 6,
                color: chipColor, fontFamily: FONT_MONO, fontSize: 10.5, fontWeight: 500,
                letterSpacing: '.04em', textTransform: 'uppercase', alignSelf: 'flex-start',
              }}>
                <Dot color={chipColor} size={6} pulse={!offline} />
                {d?.name ?? s.device_id}
                <Icons.chevRight size={10} stroke={chipColor} />
              </button>
              {/* Dir + offline "last seen" (device's own last_seen, not the
                  session row's — it's the device that's unreachable). */}
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontFamily: FONT_MONO, fontSize: 11, color: RT.textLow }}>
                <Icons.folder size={10} stroke={RT.textLow} />
                <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{s.cwd ?? '—'}</span>
                {s.tokens != null && (
                  <>
                    <span style={{ color: RT.borderHi }}>·</span>
                    <span>{Math.round(s.tokens / 1000)}K</span>
                  </>
                )}
                {usageLabel != null && (
                  <>
                    <span style={{ color: RT.borderHi }}>·</span>
                    <span title="Cumulative effective tokens">{usageLabel} eff</span>
                  </>
                )}
                {offline && (
                  <>
                    <span style={{ color: RT.borderHi }}>·</span>
                    <span style={{ color: RT.amber }}>last seen {formatRelativeTime(d?.last_seen)}</span>
                  </>
                )}
              </div>
              {/* Actions: Preview | Restart | Stop | RC — gated on field
                  presence (pid/tmux/rc_url), not on isExternal alone. */}
              <div onClick={(e) => e.stopPropagation()} style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
                {!isExternal && (
                  <>
                    <button
                      style={mobileActionBtn()}
                      onClick={openPreview}
                      title="Show terminal output"
                    >
                      <Icons.search size={13} stroke={RT.textDim} /> Preview
                    </button>
                    <button
                      style={mobileActionBtn()}
                      disabled={!!pending[`restart-${key}`] || offline}
                      onClick={() => guard(`restart-${key}`, () => api.restart(s.device_id, name))}
                      title="Restart this session"
                    >
                      <Icons.refresh size={13} stroke={RT.green} /> Restart
                    </button>
                  </>
                )}
                {isAdopted && (
                  <>
                    <button
                      style={mobileActionBtn()}
                      onClick={openPreview}
                      title="Show terminal output"
                    >
                      <Icons.search size={13} stroke={RT.textDim} /> Preview
                    </button>
                    {rowRcUrl ? (
                      <button
                        style={mobileActionBtn()}
                        onClick={() => window.open(rowRcUrl, '_blank', 'noopener,noreferrer')}
                        title="Open on claude.ai"
                      >
                        <Icons.link size={13} stroke={RT.textDim} /> Open on claude.ai
                      </button>
                    ) : (
                      <button
                        style={mobileActionBtn()}
                        disabled={!!pending[`rc-${key}`]}
                        onClick={() => handleEnableRc(key, s.device_id, s.tmux!.session_name)}
                        title="Enable Remote Control"
                      >
                        <Icons.refresh size={13} stroke={RT.amber} /> Enable RC
                      </button>
                    )}
                  </>
                )}
                {/* ⋯ more menu — Copy session ID / Open Claude Code / Unstick.
                    A non-adopted external row (no tmux pane, nothing in
                    the menu would work) gets no menu at all. */}
                {(!isExternal || isAdopted) && (
                  <MoreMenu
                    sessionId={s.session_id}
                    rcUrl={rowRcUrl}
                    isExternal={isExternal}
                    pending={!!pending[`unstick-${key}`]}
                    onUnstick={() => guard(`unstick-${key}`, () => api.unstick(s.device_id, name))}
                  />
                )}
                {(!isExternal || canStopExternal) && (
                  <button
                    style={{ background: RT.panel, border: `1px solid ${RT.border}`, borderRadius: 7, width: 36, height: 36, display: 'inline-flex', alignItems: 'center', justifyContent: 'center', cursor: 'pointer', marginLeft: 'auto' }}
                    disabled={!!pending[`stop-${key}`] || offline}
                    onClick={() => guard(`stop-${key}`, () => api.stop(s.device_id, name, isExternal ? { external: true, pid: s.pid ?? undefined } : undefined))}
                    title="Stop this session"
                  >
                    <Icons.stop size={12} stroke={RT.red} />
                  </button>
                )}
              </div>
              {rcErrors[key] && (
                <div style={{ fontFamily: FONT_MONO, fontSize: 10.5, color: RT.red }}>
                  {rcErrors[key]}
                </div>
              )}
            </div>
          );
        })}
      </div>
      {preview && (
        <PreviewModal
          deviceId={preview.deviceId}
          name={preview.name}
          sessionId={preview.sessionId}
          mode={preview.mode}
          onClose={() => setPreview(null)}
        />
      )}
    </div>
  );
}

// Per-row ⋯ menu — Copy session ID / Open Claude Code / Unstick. Each item
// gates on the field it needs rather than on isExternal alone, mirroring
// the other row actions above: Copy needs a session_id, Open Claude Code
// needs an rc_url, Unstick is a launcher concept (not offered for an
// external/adopted row, same as SessionRow.tsx).
interface MoreMenuProps {
  sessionId?: string;
  rcUrl?: string | null;
  isExternal: boolean;
  pending: boolean;
  onUnstick: () => void;
}
function MoreMenu({ sessionId, rcUrl, isExternal, pending, onUnstick }: MoreMenuProps) {
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState<React.CSSProperties | null>(null);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const off = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    if (open) document.addEventListener('mousedown', off);
    return () => document.removeEventListener('mousedown', off);
  }, [open]);

  const handleCopy = async () => {
    if (!sessionId) return;
    try { await navigator.clipboard.writeText(sessionId); } catch { /* ignore */ }
    setOpen(false);
  };
  const handleOpenUrl = () => {
    if (!rcUrl) return;
    window.open(rcUrl, '_blank', 'noopener,noreferrer');
    setOpen(false);
  };
  const handleUnstickClick = () => {
    setOpen(false);
    onUnstick();
  };

  return (
    <div ref={ref} style={{ position: 'relative' }} onClick={(e) => e.stopPropagation()}>
      <button
        onClick={() => {
          if (!open && ref.current) setPos(fixedMenuPos(ref.current));
          setOpen((o) => !o);
        }}
        disabled={pending}
        title="More options"
        style={{
          background: RT.panel, border: `1px solid ${RT.border}`, borderRadius: 7,
          width: 36, height: 36, display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
          cursor: pending ? 'default' : 'pointer', opacity: pending ? 0.5 : 1,
        }}
      >
        <Icons.more size={14} stroke={RT.textDim} />
      </button>
      {open && (
        <div style={{
          ...(pos ?? {}),
          background: RT.panel, border: `1px solid ${RT.borderHi}`,
          borderRadius: 8, padding: 4, zIndex: Z.menu,
          boxShadow: '0 8px 24px rgba(0,0,0,.4)', minWidth: 180,
        }}>
          <MenuItem
            icon={<Icons.copy size={12} stroke={RT.textDim} />}
            label="Copy session ID"
            onClick={handleCopy}
            disabled={!sessionId}
          />
          <MenuItem
            icon={<Icons.link size={12} stroke={RT.textDim} />}
            label="Open Claude Code"
            onClick={handleOpenUrl}
            disabled={!rcUrl}
          />
          {!isExternal && (
            <MenuItem
              icon={<Icons.refresh size={12} stroke={RT.amber} />}
              label="Unstick"
              onClick={handleUnstickClick}
            />
          )}
        </div>
      )}
    </div>
  );
}

function MenuItem({ icon, label, onClick, disabled }: { icon: React.ReactNode; label: string; onClick: () => void; disabled?: boolean }) {
  return (
    <button
      style={{
        width: '100%', textAlign: 'left',
        background: 'transparent', border: 'none', borderRadius: 5,
        padding: '9px 11px', cursor: disabled ? 'default' : 'pointer',
        color: disabled ? RT.textLow : RT.text, fontFamily: 'inherit',
        fontSize: 12, display: 'flex', alignItems: 'center', gap: 8,
        opacity: disabled ? 0.5 : 1,
      }}
      onClick={disabled ? undefined : onClick}
      onMouseEnter={(e) => { if (!disabled) (e.currentTarget as HTMLButtonElement).style.background = RT.bgRaised; }}
      onMouseLeave={(e) => { (e.currentTarget as HTMLButtonElement).style.background = 'transparent'; }}
    >
      {icon} {label}
    </button>
  );
}
