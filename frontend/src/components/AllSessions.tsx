// AllSessions.tsx — Sessions tab, rendered from the hub-wide fleet store
// (useFleet, Task 12). Sessions on a currently-offline device still render
// here (dimmed, with a "last seen" age) instead of being silently dropped.
import { useState } from 'react';
import { RT, FONT_MONO, tintFor, hueForId } from '../tokens';
import { Icons, Dot, StatusPill, ExternalBadge } from './primitives';
import { MobileHeader } from './MobileHeader';
import { mobileActionBtn } from './mobileActionBtn';
import { useFleet } from '../hooks/useFleet';
import type { FleetDevice, FleetSession } from '../hooks/useFleet';
import { api } from '../api';
import { PreviewModal } from './PreviewModal';

interface AllSessionsProps {
  onOpenDevice: (id: string) => void;
}

interface PreviewState { deviceId: string; name: string; sessionId?: string; }

// last_seen is a unix-epoch-seconds float from store.py, not the ISO
// strings Activity.tsx's relativeTime() expects — a separate small
// formatter for this shape.
function formatRelativeTime(epochSeconds: number | null | undefined): string {
  if (epochSeconds == null) return 'unknown';
  const diffMs = Date.now() - epochSeconds * 1000;
  if (isNaN(diffMs)) return 'unknown';
  const mins = Math.floor(diffMs / 60_000);
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  if (days === 1) return 'yesterday';
  return `${days}d ago`;
}

export function AllSessions({ onOpenDevice }: AllSessionsProps) {
  const { devices, sessions, usingFallback } = useFleet();
  const [pending, setPending] = useState<Record<string, boolean>>({});
  const [preview, setPreview] = useState<PreviewState | null>(null);

  const guard = async (key: string, fn: () => Promise<unknown>) => {
    if (pending[key]) return;
    setPending((p) => ({ ...p, [key]: true }));
    try { await fn(); } finally {
      setPending((p) => ({ ...p, [key]: false }));
    }
  };

  const deviceById = new Map<string, FleetDevice>(devices.map((d) => [d.id, d]));

  // needs_attention sorts first, then most-recently-seen.
  const sortedSessions = [...sessions].sort((a, b) => {
    if (a.needs_attention !== b.needs_attention) return a.needs_attention ? -1 : 1;
    return (b.last_seen ?? 0) - (a.last_seen ?? 0);
  });

  const deviceCount = new Set(sessions.map((s) => s.device_id)).size;

  return (
    <div style={{ flex: 1, overflow: 'auto', display: 'flex', flexDirection: 'column' }}>
      <MobileHeader
        subtitle={`${sessions.length} session${sessions.length !== 1 ? 's' : ''} · across ${deviceCount} device${deviceCount !== 1 ? 's' : ''}${usingFallback ? ' · polling' : ''}`}
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
          // Without tmux/pid detail (not carried by the fleet roll-up),
          // an external session's terminal can't reliably be addressed —
          // it's shown read-only, same as an unadopted external row.
          const canOpenTerminal = !isExternal;

          return (
            <div
              key={key}
              onClick={canOpenTerminal ? () => setPreview({ deviceId: s.device_id, name, sessionId: s.session_id }) : undefined}
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
              {/* Dir + offline "last seen" */}
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontFamily: FONT_MONO, fontSize: 11, color: RT.textLow }}>
                <Icons.folder size={10} stroke={RT.textLow} />
                <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{s.cwd ?? '—'}</span>
                {offline && (
                  <>
                    <span style={{ color: RT.borderHi }}>·</span>
                    <span style={{ color: RT.amber }}>last seen {formatRelativeTime(s.last_seen)}</span>
                  </>
                )}
              </div>
              {/* Actions: Preview | Restart | Stop — hidden for external/offline
                  rows, since the fleet roll-up doesn't carry the pid/tmux
                  detail those need to be addressed safely. */}
              {!isExternal && (
                <div onClick={(e) => e.stopPropagation()} style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
                  <button
                    style={mobileActionBtn()}
                    onClick={() => setPreview({ deviceId: s.device_id, name, sessionId: s.session_id })}
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
                  <button
                    style={{ background: RT.panel, border: `1px solid ${RT.border}`, borderRadius: 7, width: 36, height: 36, display: 'inline-flex', alignItems: 'center', justifyContent: 'center', cursor: 'pointer', marginLeft: 'auto' }}
                    disabled={!!pending[`stop-${key}`] || offline}
                    onClick={() => guard(`stop-${key}`, () => api.stop(s.device_id, name))}
                    title="Stop this session"
                  >
                    <Icons.stop size={12} stroke={RT.red} />
                  </button>
                </div>
              )}
              {isExternal && (
                <span style={{ fontFamily: FONT_MONO, fontSize: 10.5, color: RT.textLow, fontStyle: 'italic' }}>
                  external session
                </span>
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
          onClose={() => setPreview(null)}
        />
      )}
    </div>
  );
}
