// ScheduledRow.tsx — V5 full-width 3-col grid with V5IconButton actions.
import { useState } from 'react';
import { RT, FONT_MONO } from '../tokens';
import { Icons } from './primitives';
import { V5IconButton } from './V5IconButton';
import { api } from '../api';
import type { Schedule } from '../types';

export interface ScheduledRowProps {
  s: Schedule;
  deviceId: string;
  mobile?: boolean;
  onChanged: () => void;
  onEdit: (s: Schedule) => void;
}

export function ScheduledRow({ s, deviceId, mobile = false, onChanged, onEdit }: ScheduledRowProps) {
  const [pending, setPending] = useState(false);

  async function withPending(fn: () => Promise<void>) {
    if (pending) return;
    setPending(true);
    try { await fn(); } finally { setPending(false); }
  }

  const handleToggle = () =>
    withPending(async () => {
      await api.schedUpdate(deviceId, { id: s.id, enabled: !s.enabled });
      onChanged();
    });

  const handleFire = () =>
    withPending(async () => {
      await api.schedFire(deviceId, s.id);
      onChanged();
    });

  const handleEdit = () => { if (!pending) onEdit(s); };

  const handleDelete = () =>
    withPending(async () => {
      if (!window.confirm(`Delete schedule "${s.name}"?`)) return;
      await api.schedDelete(deviceId, s.id);
      onChanged();
    });

  return (
    <div style={{
      background: RT.card, border: `1px solid ${RT.border}`,
      borderRadius: 10, padding: mobile ? 14 : '14px 18px',
      display: 'grid',
      gridTemplateColumns: mobile ? '1fr' : '1.5fr 1fr auto',
      gap: 16, alignItems: 'center',
    }}>
      {/* Col 1: Name + badge + cron */}
      <div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 5 }}>
          <div style={{
            fontSize: 14, fontWeight: 600, letterSpacing: '-.005em',
            whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', flex: 1, minWidth: 0,
          }}>
            {s.name}
          </div>
          {/* Clickable enabled/paused badge */}
          <button
            onClick={handleToggle}
            disabled={pending}
            title={s.enabled ? 'Click to pause' : 'Click to enable'}
            style={{
              fontSize: 9.5, fontFamily: FONT_MONO, letterSpacing: '.06em',
              textTransform: 'uppercase', cursor: pending ? 'default' : 'pointer',
              color: s.enabled ? RT.green : RT.textLow,
              padding: '2px 7px', borderRadius: 4,
              background: s.enabled ? 'oklch(0.66 0.10 150 / 0.12)' : 'rgba(255,255,255,.04)',
              border: `1px solid ${s.enabled ? 'oklch(0.66 0.10 150 / 0.35)' : RT.border}`,
              flex: 'none', opacity: pending ? 0.5 : 1,
            }}
          >
            {s.enabled ? 'enabled' : 'paused'}
          </button>
        </div>
        <div style={{
          fontSize: 11, fontFamily: FONT_MONO, color: RT.textLow,
          display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap',
        }}>
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
            <Icons.clock size={10} stroke={RT.textLow} /> {s.cron}
          </span>
          {s.mode && (
            <>
              <span style={{ color: RT.borderHi }}>·</span>
              <span>{s.mode}</span>
            </>
          )}
        </div>
      </div>

      {/* Col 2: Dir + next run */}
      <div style={{
        fontSize: 11, fontFamily: FONT_MONO, color: RT.textDim,
        display: 'flex', alignItems: 'center', gap: 6, minWidth: 0,
      }}>
        {s.workdir && (
          <>
            <Icons.folder size={10} stroke={RT.textLow} />
            <span style={{ whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
              {s.workdir.replace(/\/$/, '').split('/').pop() || s.workdir}
            </span>
            <span style={{ color: RT.borderHi }}>·</span>
          </>
        )}
        {s.next_run && (
          <span style={{ whiteSpace: 'nowrap', color: RT.textLow }}>
            next {new Date(s.next_run).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })}
          </span>
        )}
      </div>

      {/* Col 3: Actions — Run now (green), Edit (terminal), Delete (red) */}
      <div style={{ display: 'flex', gap: 6, justifyContent: 'flex-end' }}>
        <V5IconButton label="Run now" accent={RT.green} pending={pending} onClick={handleFire}>
          <Icons.play size={12} />
        </V5IconButton>
        <V5IconButton label="Edit schedule" pending={pending} onClick={handleEdit}>
          <Icons.terminal size={13} stroke={RT.textDim} />
        </V5IconButton>
        <V5IconButton label="Delete schedule" accent={RT.red} pending={pending} onClick={handleDelete}>
          <Icons.stop size={11} />
        </V5IconButton>
      </div>
    </div>
  );
}
