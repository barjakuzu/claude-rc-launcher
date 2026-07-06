// AllScheduled.tsx — cross-device flattened schedules list (V5AllScheduled port).
import { useState } from 'react';
import { RT, FONT_MONO, tintFor, hueForId, Z } from '../tokens';
import { Icons, Dot } from './primitives';
import { MobileHeader } from './MobileHeader';
import { mobileActionBtn } from './mobileActionBtn';
import { ScheduleModal } from './ScheduleModal';
import { DevicePicker, rewriteHomePaths, type DevicePickResult } from './DevicePicker';
import { useAllSchedules } from '../useCrossDevice';
import { api } from '../api';
import type { DeviceCard, Schedule } from '../types';

interface AllScheduledProps {
  cards: DeviceCard[];
}

type PickerEntry = { deviceId: string; schedule: Schedule; mode: 'copy' | 'move' };

export function AllScheduled({ cards }: AllScheduledProps) {
  const items = useAllSchedules(cards, true);
  const [editEntry, setEditEntry] = useState<{ deviceId: string; schedule: Schedule } | null>(null);
  const [newDeviceId, setNewDeviceId] = useState<string | null>(null);
  const [pickerEntry, setPickerEntry] = useState<PickerEntry | null>(null);
  const [moreOpenId, setMoreOpenId] = useState<string | null>(null);

  async function handlePick(result: DevicePickResult, entry: PickerEntry) {
    const targetDeviceId = result.deviceId;
    const s = entry.schedule;
    // If the source has an instructions_file, fetch its content from the
    // source device and inline it as the target's `prompt`. The path on the
    // source won't exist on the target — inlining makes the copy standalone.
    let prompt: string = s.prompt ?? '';
    let instructions_file: string | undefined = s.instructions_file || undefined;
    if (instructions_file) {
      try {
        const r = await api.schedInstructions(entry.deviceId, s.id);
        if (r && typeof r.content === 'string' && r.content) {
          prompt = r.content;
          instructions_file = undefined;
        }
      } catch { /* fall through with path-only */ }
    }
    let workdir = s.workdir ?? '';
    if (result.rewritePaths) {
      const src = cards.find((c) => c.id === entry.deviceId)?.home_dir ?? '';
      const tgt = cards.find((c) => c.id === targetDeviceId)?.home_dir ?? '';
      if (src && tgt && src !== tgt) {
        workdir = rewriteHomePaths(workdir, src, tgt);
        prompt  = rewriteHomePaths(prompt,  src, tgt);
        if (instructions_file) {
          instructions_file = rewriteHomePaths(instructions_file, src, tgt);
        }
      }
    }
    const body = {
      name: s.name,
      cron: s.cron,
      prompt,
      instructions_file,
      workdir,
      mode: s.mode ?? 'c',
      model: s.model ?? undefined,
      enabled: s.enabled ?? false,
    };
    try {
      const res = await api.schedCreate(targetDeviceId, body);
      if (res && res.ok === false) throw new Error(res.message ?? 'create failed');
      if (entry.mode === 'move') {
        await api.schedDelete(entry.deviceId, s.id).catch((err) => {
          console.warn('Copy succeeded but delete failed (move):', err);
        });
      }
      setPickerEntry(null);
    } catch (e) {
      alert(`Failed to ${entry.mode} schedule: ${(e as Error).message}`);
    }
  }

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
              <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
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
                {cards.length > 1 && (
                  <div style={{ position: 'relative' }}>
                    <button
                      style={mobileActionBtn()}
                      onClick={() => setMoreOpenId(moreOpenId === d.id + s.id ? null : d.id + s.id)}
                    >
                      <Icons.more size={13} stroke={RT.textDim} />
                    </button>
                    {moreOpenId === d.id + s.id && (
                      <div style={{
                        position: 'absolute', bottom: '100%', left: 0, marginBottom: 4,
                        background: RT.panel, border: `1px solid ${RT.borderHi}`,
                        borderRadius: 8, padding: 4, zIndex: Z.menu,
                        boxShadow: '0 8px 24px rgba(0,0,0,.4)', minWidth: 160,
                      }}>
                        <button
                          style={moreItemStyle}
                          onClick={() => { setMoreOpenId(null); setPickerEntry({ deviceId: d.id, schedule: s, mode: 'copy' }); }}
                        >
                          <Icons.copy size={11} stroke={RT.textDim} /> Copy to…
                        </button>
                        <button
                          style={moreItemStyle}
                          onClick={() => { setMoreOpenId(null); setPickerEntry({ deviceId: d.id, schedule: s, mode: 'move' }); }}
                        >
                          <Icons.share size={11} stroke={RT.textDim} /> Move to…
                        </button>
                        <div style={{ height: 1, background: RT.border, margin: '3px 0' }} />
                        <button
                          style={{ ...moreItemStyle, color: RT.red }}
                          onClick={() => {
                            setMoreOpenId(null);
                            if (window.confirm(`Delete schedule "${s.name}"?`)) {
                              api.schedDelete(d.id, s.id);
                            }
                          }}
                        >
                          <Icons.stop size={11} stroke={RT.red} /> Delete
                        </button>
                      </div>
                    )}
                  </div>
                )}
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

      {/* Device picker for Copy/Move */}
      {pickerEntry && (() => {
        const s = pickerEntry.schedule;
        const pathHints: string[] = [];
        if (s.workdir) pathHints.push(`workdir "${s.workdir}"`);
        if (s.instructions_file) pathHints.push(`instructions file "${s.instructions_file}"`);
        const caveat = pathHints.length > 0
          ? `Note: ${pathHints.join(' and ')} must exist on the target device for the schedule to run.`
          : undefined;
        return (
          <DevicePicker
            cards={cards}
            excludeDeviceId={pickerEntry.deviceId}
            title={pickerEntry.mode === 'copy' ? 'Copy schedule to…' : 'Move schedule to…'}
            caveat={caveat}
            sourceHomeDir={cards.find((c) => c.id === pickerEntry.deviceId)?.home_dir}
            contentSample={`${s.workdir ?? ''} ${s.prompt ?? ''} ${s.instructions_file ?? ''}`}
            onPick={(res) => handlePick(res, pickerEntry)}
            onClose={() => setPickerEntry(null)}
          />
        );
      })()}
    </div>
  );
}

const moreItemStyle: React.CSSProperties = {
  width: '100%', textAlign: 'left',
  background: 'transparent', border: 'none', borderRadius: 5,
  padding: '8px 11px', cursor: 'pointer', color: RT.text, fontFamily: 'inherit',
  fontSize: 12, display: 'flex', alignItems: 'center', gap: 8,
};
