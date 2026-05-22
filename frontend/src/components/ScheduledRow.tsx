// ScheduledRow.tsx — RScheduledRow with per-row CRUD actions.
// Card layout: name + ENABLED/PAUSED badge, cron line with clock icon, action buttons.
import { useState } from 'react';
import { RT, FONT_MONO } from '../tokens';
import { Icons } from './primitives';
import { btn } from './btn';
import { api } from '../api';
import type { Schedule } from '../types';

export interface ScheduledRowProps {
  s: Schedule;
  deviceId: string;
  onChanged: () => void;
  onEdit: (s: Schedule) => void;
}

export function ScheduledRow({ s, deviceId, onChanged, onEdit }: ScheduledRowProps) {
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

  const handleEdit = () => {
    if (!pending) onEdit(s);
  };

  const handleDelete = () =>
    withPending(async () => {
      if (!window.confirm(`Delete schedule "${s.name}"?`)) return;
      await api.schedDelete(deviceId, s.id);
      onChanged();
    });

  return (
    <div style={{
      background: RT.card,
      border: `1px solid ${RT.border}`,
      borderRadius: 8,
      padding: '10px 12px',
      display: 'flex',
      flexDirection: 'column',
      gap: 5,
    }}>
      {/* Name + enabled badge */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <div style={{ fontSize: 12, fontWeight: 600, flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {s.name}
        </div>
        <span style={{
          fontSize: 9,
          fontFamily: FONT_MONO,
          letterSpacing: '.06em',
          textTransform: 'uppercase',
          color: s.enabled ? RT.green : RT.textLow,
          padding: '1px 6px',
          borderRadius: 3,
          background: s.enabled ? 'oklch(0.66 0.10 150 / 0.12)' : 'rgba(255,255,255,.04)',
          flex: 'none',
        }}>
          {s.enabled ? 'ENABLED' : 'PAUSED'}
        </span>
      </div>

      {/* Cron + next run */}
      <div style={{
        fontSize: 10,
        fontFamily: FONT_MONO,
        color: RT.textDim,
        display: 'flex',
        alignItems: 'center',
        gap: 6,
      }}>
        <Icons.clock size={9} stroke={RT.textLow} />
        <span>{s.cron}</span>
        {s.next_run && (
          <span style={{ color: RT.textLow }}>
            (next: {new Date(s.next_run).toLocaleString()})
          </span>
        )}
      </div>

      {/* Action buttons */}
      <div style={{ display: 'flex', gap: 4, justifyContent: 'flex-end', marginTop: 2 }}>
        {/* Enable/Pause toggle */}
        <button
          onClick={handleToggle}
          disabled={pending}
          title={s.enabled ? 'Pause schedule' : 'Enable schedule'}
          style={{
            ...btn('mini'),
            opacity: pending ? 0.5 : 1,
            color: s.enabled ? RT.amber : RT.green,
          }}
        >
          {s.enabled
            ? <Icons.pause size={11} />
            : <Icons.play size={11} />
          }
        </button>

        {/* Fire now */}
        <button
          onClick={handleFire}
          disabled={pending}
          title="Run now"
          style={{
            ...btn('mini'),
            opacity: pending ? 0.5 : 1,
            color: RT.green,
          }}
        >
          <Icons.forward size={11} />
        </button>

        {/* Edit */}
        <button
          onClick={handleEdit}
          disabled={pending}
          title="Edit schedule"
          style={{
            ...btn('mini'),
            opacity: pending ? 0.5 : 1,
            color: RT.textDim,
          }}
        >
          <Icons.terminal size={11} />
        </button>

        {/* Delete */}
        <button
          onClick={handleDelete}
          disabled={pending}
          title="Delete schedule"
          style={{
            ...btn('mini'),
            opacity: pending ? 0.5 : 1,
            color: RT.red,
          }}
        >
          <Icons.stop size={11} />
        </button>
      </div>
    </div>
  );
}
