// AllSessions.tsx — cross-device flattened sessions list (V5AllSessions port).
import { useState } from 'react';
import { RT, FONT_MONO, tintFor, hueForId } from '../tokens';
import { Icons, Dot, StatusPill } from './primitives';
import { MobileHeader } from './MobileHeader';
import { mobileActionBtn } from './mobileActionBtn';
import { useAllSessions } from '../useCrossDevice';
import { api } from '../api';
import type { DeviceCard } from '../types';

interface AllSessionsProps {
  cards: DeviceCard[];
  onOpenDevice: (id: string) => void;
}

export function AllSessions({ cards, onOpenDevice }: AllSessionsProps) {
  const items = useAllSessions(cards, true);
  const [pending, setPending] = useState<Record<string, boolean>>({});

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
              {/* Actions */}
              <div style={{ display: 'flex', gap: 6 }}>
                {s.url && (
                  <button
                    style={mobileActionBtn()}
                    onClick={() => window.open(s.url, '_blank')}
                  >
                    <Icons.link size={13} stroke={RT.textDim} /> Preview
                  </button>
                )}
                <button
                  style={mobileActionBtn()}
                  disabled={!!pending[`resume-${key}`]}
                  onClick={() => guard(`resume-${key}`, () => api.restart(d.id, s.name))}
                >
                  <Icons.refresh size={13} stroke={RT.green} /> Resume
                </button>
                <button
                  style={{ background: RT.panel, border: `1px solid ${RT.border}`, borderRadius: 7, width: 36, height: 36, display: 'inline-flex', alignItems: 'center', justifyContent: 'center', cursor: 'pointer', marginLeft: 'auto' }}
                  disabled={!!pending[`stop-${key}`]}
                  onClick={() => guard(`stop-${key}`, () => api.stop(d.id, s.name))}
                >
                  <Icons.stop size={12} stroke={RT.red} />
                </button>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
