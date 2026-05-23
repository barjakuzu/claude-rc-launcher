// AllSessions.tsx — cross-device flattened sessions list (V5AllSessions port).
import { useEffect, useRef, useState } from 'react';
import { RT, FONT_MONO, tintFor, hueForId } from '../tokens';
import { Icons, Dot, StatusPill } from './primitives';
import { MobileHeader } from './MobileHeader';
import { mobileActionBtn } from './mobileActionBtn';
import { useAllSessions } from '../useCrossDevice';
import { api } from '../api';
import { PreviewModal } from './PreviewModal';
import type { DeviceCard } from '../types';

interface AllSessionsProps {
  cards: DeviceCard[];
  onOpenDevice: (id: string) => void;
}

interface PreviewState { deviceId: string; name: string; }

export function AllSessions({ cards, onOpenDevice }: AllSessionsProps) {
  const items = useAllSessions(cards, true);
  const [pending, setPending] = useState<Record<string, boolean>>({});
  const [preview, setPreview] = useState<PreviewState | null>(null);

  const guard = async (key: string, fn: () => Promise<unknown>) => {
    if (pending[key]) return;
    setPending((p) => ({ ...p, [key]: true }));
    try { await fn(); } finally {
      setPending((p) => ({ ...p, [key]: false }));
    }
  };

  const deviceCount = new Set(items.map((i) => i.device.id)).size;

  return (
    <div style={{ flex: 1, overflow: 'auto', display: 'flex', flexDirection: 'column' }}>
      <MobileHeader
        subtitle={`${items.length} active · across ${deviceCount} device${deviceCount !== 1 ? 's' : ''}`}
        title="Sessions"
        right={
          <button style={{ background: RT.panel, border: `1px solid ${RT.border}`, borderRadius: 7, width: 34, height: 34, display: 'flex', alignItems: 'center', justifyContent: 'center', cursor: 'pointer' }}>
            <Icons.filter size={14} stroke={RT.textDim} />
          </button>
        }
      />
      <div style={{ padding: 12, display: 'flex', flexDirection: 'column', gap: 8 }}>
        {items.length === 0 && (
          <div style={{ padding: 32, textAlign: 'center', color: RT.textLow, fontFamily: FONT_MONO, fontSize: 13, border: `1px dashed ${RT.border}`, borderRadius: 10 }}>
            No active sessions across devices.
          </div>
        )}
        {items.map(({ device: d, session: s }) => {
          const hue = hueForId(d.id);
          const chipColor = tintFor(hue, 0.70, 0.10);
          const key = d.id + (s.sessionId ?? s.name);
          return (
            <div key={key} style={{
              background: RT.card, border: `1px solid ${RT.border}`,
              borderRadius: 10, padding: 12,
              display: 'flex', flexDirection: 'column', gap: 8,
            }}>
              {/* Name + status */}
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <div style={{ flex: 1, fontSize: 14, fontWeight: 600, letterSpacing: '-.005em', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{s.name}</div>
                <StatusPill status={s.status ?? 'idle'} />
              </div>
              {/* Device chip */}
              <button onClick={() => onOpenDevice(d.id)} style={{
                background: 'transparent', border: 'none', cursor: 'pointer', padding: 0,
                display: 'inline-flex', alignItems: 'center', gap: 6,
                color: chipColor, fontFamily: FONT_MONO, fontSize: 10.5, fontWeight: 500,
                letterSpacing: '.04em', textTransform: 'uppercase', alignSelf: 'flex-start',
              }}>
                <Dot color={chipColor} size={6} pulse={d.online} />
                {d.name}
                <Icons.chevRight size={10} stroke={chipColor} />
              </button>
              {/* Dir + tokens */}
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontFamily: FONT_MONO, fontSize: 11, color: RT.textLow }}>
                <Icons.folder size={10} stroke={RT.textLow} />
                <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{s.workdir ?? '—'}</span>
                {s.tokens != null && (
                  <>
                    <span style={{ color: RT.borderHi }}>·</span>
                    <span>{Math.round((s.tokens ?? 0) / 1000)}K</span>
                  </>
                )}
              </div>
              {/* Actions: Preview (terminal) | Restart | More (⋯) | Stop */}
              <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
                <button
                  style={mobileActionBtn()}
                  onClick={() => setPreview({ deviceId: d.id, name: s.name })}
                  title="Show terminal output"
                >
                  <Icons.search size={13} stroke={RT.textDim} /> Preview
                </button>
                <button
                  style={mobileActionBtn()}
                  disabled={!!pending[`restart-${key}`]}
                  onClick={() => guard(`restart-${key}`, () => api.restart(d.id, s.name))}
                  title="Restart this session"
                >
                  <Icons.refresh size={13} stroke={RT.green} /> Restart
                </button>
                <MoreMenu
                  deviceId={d.id}
                  sessionName={s.name}
                  sessionId={s.sessionId}
                  url={s.url}
                  pending={!!pending[`unstick-${key}`]}
                  onUnstick={() => guard(`unstick-${key}`, () => api.unstick(d.id, s.name))}
                />
                <button
                  style={{ background: RT.panel, border: `1px solid ${RT.border}`, borderRadius: 7, width: 36, height: 36, display: 'inline-flex', alignItems: 'center', justifyContent: 'center', cursor: 'pointer', marginLeft: 'auto' }}
                  disabled={!!pending[`stop-${key}`]}
                  onClick={() => guard(`stop-${key}`, () => api.stop(d.id, s.name))}
                  title="Stop this session"
                >
                  <Icons.stop size={12} stroke={RT.red} />
                </button>
              </div>
            </div>
          );
        })}
      </div>
      {preview && (
        <PreviewModal
          deviceId={preview.deviceId}
          name={preview.name}
          onClose={() => setPreview(null)}
        />
      )}
    </div>
  );
}

// Per-row ⋯ menu — Copy session ID, Open Claude Code URL, Unstick.
interface MoreMenuProps {
  deviceId: string;
  sessionName: string;
  sessionId?: string;
  url?: string;
  pending: boolean;
  onUnstick: () => void;
}
function MoreMenu({ sessionName, sessionId, url, pending, onUnstick }: MoreMenuProps) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const off = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    if (open) document.addEventListener('mousedown', off);
    return () => document.removeEventListener('mousedown', off);
  }, [open]);

  const copyValue = sessionId ?? url ?? sessionName;
  const handleCopy = async () => {
    try { await navigator.clipboard.writeText(copyValue); } catch { /* ignore */ }
    setOpen(false);
  };
  const handleOpenUrl = () => {
    if (url) window.open(url, '_blank');
    setOpen(false);
  };
  const handleUnstickClick = () => {
    setOpen(false);
    onUnstick();
  };

  return (
    <div ref={ref} style={{ position: 'relative' }}>
      <button
        onClick={() => setOpen((o) => !o)}
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
          position: 'absolute', bottom: '100%', right: 0, marginBottom: 4,
          background: RT.panel, border: `1px solid ${RT.borderHi}`,
          borderRadius: 8, padding: 4, zIndex: 20,
          boxShadow: '0 8px 24px rgba(0,0,0,.4)', minWidth: 180,
        }}>
          <MenuItem icon={<Icons.copy size={12} stroke={RT.textDim} />} label="Copy session ID" onClick={handleCopy} />
          <MenuItem
            icon={<Icons.link size={12} stroke={RT.textDim} />}
            label="Open Claude Code"
            onClick={handleOpenUrl}
            disabled={!url}
          />
          <MenuItem
            icon={<Icons.refresh size={12} stroke={RT.amber} />}
            label="Unstick"
            onClick={handleUnstickClick}
          />
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
