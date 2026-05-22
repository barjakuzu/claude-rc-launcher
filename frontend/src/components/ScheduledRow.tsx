// ScheduledRow.tsx — RScheduledRow ported from variant-ops-refined.jsx lines 585-607.
// Read-only this task; CRUD is wired in a later task.
import { RT, FONT_MONO } from '../tokens';
import { Icons } from './primitives';
import type { Schedule } from '../types';

export interface ScheduledRowProps {
  s: Schedule;
}

export function ScheduledRow({ s }: ScheduledRowProps) {
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
        <div style={{ fontSize: 12, fontWeight: 600, flex: 1 }}>{s.name}</div>
        <span style={{
          fontSize: 9,
          fontFamily: FONT_MONO,
          letterSpacing: '.06em',
          textTransform: 'uppercase',
          color: s.enabled ? RT.green : RT.textLow,
          padding: '1px 6px',
          borderRadius: 3,
          background: s.enabled ? 'oklch(0.66 0.10 150 / 0.12)' : 'rgba(255,255,255,.04)',
        }}>
          {s.enabled ? 'ENABLED' : 'PAUSED'}
        </span>
      </div>

      {/* Cron + schedule description */}
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
    </div>
  );
}
