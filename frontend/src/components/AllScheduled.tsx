// AllScheduled.tsx — cross-device flattened schedules list (V5AllScheduled port).
import { useState } from 'react';
import { RT, FONT_MONO, tintFor, hueForId } from '../tokens';
import { Icons, Dot } from './primitives';
import { MobileHeader } from './MobileHeader';
import { mobileActionBtn } from './mobileActionBtn';
import { ScheduleModal } from './ScheduleModal';
import { useAllSchedules } from '../useCrossDevice';
import { api } from '../api';
import type { DeviceCard, Schedule } from '../types';

interface AllScheduledProps {
  cards: DeviceCard[];
}

export function AllScheduled({ cards }: AllScheduledProps) {
  const items = useAllSchedules(cards, true);
  const [editEntry, setEditEntry] = useState<{ deviceId: string; schedule: Schedule } | null>(null);
  const [newDeviceId, setNewDeviceId] = useState<string | null>(null);

  const handleNew = () => {
    if (cards.length === 0) return;
    setNewDeviceId(cards[0].id);
  };

  return (
    <div style={{ flex: 1, overflow: 'auto', display: 'flex', flexDirection: 'column' }}>
      <MobileHeader
        subtitle={`${items.length} task${items.length !== 1 ? 's' : ''} across devices`}
        title="Scheduled"
        right={
          <button
            style={{ background: RT.panel, border: `1px solid ${RT.border}`, borderRadius: 7, width: 34, height: 34, display: 'flex', alignItems: 'center', justifyContent: 'center', cursor: 'pointer', opacity: cards.length === 0 ? 0.4 : 1 }}
            disabled={cards.length === 0}
            onClick={handleNew}
          >
            <Icons.plus size={14} stroke={RT.textDim} />
          </button>
        }
      />
      <div style={{ padding: 12, display: 'flex', flexDirection: 'column', gap: 8 }}>
        {items.length === 0 && (
          <div style={{ padding: 32, textAlign: 'center', color: RT.textLow, fontFamily: FONT_MONO, fontSize: 13, border: `1px dashed ${RT.border}`, borderRadius: 10 }}>
            No scheduled tasks across devices.
          </div>
        )}
        {items.map(({ device: d, schedule: s }) => {
          const hue = hueForId(d.id);
          const chipColor = tintFor(hue, 0.70, 0.10);
          return (
            <div key={d.id + s.id} style={{
              background: RT.card, border: `1px solid ${RT.border}`,
              borderRadius: 10, padding: 12, display: 'flex', flexDirection: 'column', gap: 7,
            }}>
              {/* Name + enabled badge */}
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <div style={{ flex: 1, fontSize: 14, fontWeight: 600, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{s.name}</div>
                <span style={{
                  fontSize: 9, fontFamily: FONT_MONO, letterSpacing: '.06em', textTransform: 'uppercase',
                  color: s.enabled ? RT.green : RT.textLow,
                  padding: '2px 6px', borderRadius: 4,
                  background: s.enabled ? 'oklch(0.66 0.10 150 / 0.12)' : 'rgba(255,255,255,.04)',
                }}>{s.enabled ? 'enabled' : 'paused'}</span>
              </div>
              {/* Device chip */}
              <div style={{ display: 'inline-flex', alignItems: 'center', gap: 5, color: chipColor, fontFamily: FONT_MONO, fontSize: 10, letterSpacing: '.04em', textTransform: 'uppercase', alignSelf: 'flex-start' }}>
                <Dot color={chipColor} size={5} pulse={d.online} /> {d.name}
              </div>
              {/* Cron + label */}
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontFamily: FONT_MONO, fontSize: 11, color: RT.textLow }}>
                <Icons.clock size={10} stroke={RT.textLow} />
                <span>{s.cron}</span>
                {s.schedule_label && <span style={{ color: RT.borderHi }}>({s.schedule_label})</span>}
              </div>
              {/* Actions */}
              <div style={{ display: 'flex', gap: 6 }}>
                <button
                  style={mobileActionBtn()}
                  onClick={() => api.schedFire(d.id, s.id)}
                >
                  <Icons.play size={12} stroke={RT.green} /> Run now
                </button>
                <button
                  style={mobileActionBtn()}
                  onClick={() => setEditEntry({ deviceId: d.id, schedule: s })}
                >
                  <Icons.terminal size={13} stroke={RT.textDim} /> Edit
                </button>
              </div>
            </div>
          );
        })}
      </div>

      {/* Edit modal */}
      {editEntry && (
        <ScheduleModal
          deviceId={editEntry.deviceId}
          initial={editEntry.schedule}
          onClose={() => setEditEntry(null)}
          onSaved={() => setEditEntry(null)}
        />
      )}

      {/* New schedule modal */}
      {newDeviceId && (
        <ScheduleModal
          deviceId={newDeviceId}
          initial={null}
          onClose={() => setNewDeviceId(null)}
          onSaved={() => setNewDeviceId(null)}
        />
      )}
    </div>
  );
}
