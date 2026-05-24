// ScheduledRow.tsx — V5 full-width 3-col grid with V5IconButton actions.
import { useEffect, useRef, useState } from 'react';
import { RT, FONT_MONO } from '../tokens';
import { Icons } from './primitives';
import { V5IconButton } from './V5IconButton';
import { DevicePicker, rewriteHomePaths, type DevicePickResult } from './DevicePicker';
import { api } from '../api';
import type { Schedule, DeviceCard } from '../types';

export interface ScheduledRowProps {
  s: Schedule;
  deviceId: string;
  mobile?: boolean;
  cards: DeviceCard[];
  onChanged: () => void;
  onEdit: (s: Schedule) => void;
}

export function ScheduledRow({ s, deviceId, mobile = false, cards, onChanged, onEdit }: ScheduledRowProps) {
  const [pending, setPending] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const [pickerMode, setPickerMode] = useState<'copy' | 'move' | null>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const off = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) setMenuOpen(false);
    };
    if (menuOpen) document.addEventListener('mousedown', off);
    return () => document.removeEventListener('mousedown', off);
  }, [menuOpen]);

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

  const pathHints: string[] = [];
  if (s.workdir) pathHints.push(`workdir "${s.workdir}"`);
  if (s.instructions_file) pathHints.push(`instructions file "${s.instructions_file}"`);
  const caveat = pathHints.length > 0
    ? `Note: ${pathHints.join(' and ')} must exist on the target device for the schedule to run.`
    : undefined;

  async function handlePick(result: DevicePickResult) {
    const targetDeviceId = result.deviceId;
    setPending(true);
    try {
      // If the source has an instructions_file, fetch its content and inline
      // it as the target's `prompt`. The path on the source won't exist on
      // the target; inlining means the copy works standalone.
      let prompt: string = s.prompt ?? '';
      let instructions_file: string | undefined = s.instructions_file || undefined;
      if (instructions_file) {
        try {
          const r = await api.schedInstructions(deviceId, s.id);
          if (r && typeof r.content === 'string' && r.content) {
            prompt = r.content;
            instructions_file = undefined; // inlined — no longer needs the path
          }
        } catch {
          // Couldn't read the file (404/etc.) — fall through with the path-only
          // copy and warn the user after.
        }
      }
      let workdir = s.workdir ?? '';
      // Optionally rewrite the source home dir → target home dir in workdir
      // and prompt content (and instructions_file path if we kept it).
      if (result.rewritePaths) {
        const src = cards.find((c) => c.id === deviceId)?.home_dir ?? '';
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
      const res = await api.schedCreate(targetDeviceId, body);
      if (res && res.ok === false) throw new Error(res.message ?? 'create failed');
      if (pickerMode === 'move') {
        await api.schedDelete(deviceId, s.id).catch((err) => {
          console.warn('Copy succeeded but delete failed (move):', err);
        });
      }
      setPickerMode(null);
      onChanged();
    } catch (e) {
      alert(`Failed to ${pickerMode} schedule: ${(e as Error).message}`);
    } finally {
      setPending(false);
    }
  }

  const hasOtherDevices = cards.length > 1;

  return (
    <>
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

      {/* Col 3: Actions — Run now (primary) + ⋯ for Edit / Delete */}
      <div style={{ display: 'flex', gap: 6, justifyContent: 'flex-end' }}>
        <V5IconButton label="Run now" accent={RT.green} pending={pending} onClick={handleFire}>
          <Icons.play size={12} />
        </V5IconButton>

        <div ref={menuRef} style={{ position: 'relative' }}>
          <V5IconButton
            label="More options"
            pending={pending}
            onClick={() => setMenuOpen((o) => !o)}
          >
            <Icons.more size={14} stroke={RT.textDim} />
          </V5IconButton>

          {menuOpen && (
            <div style={{
              position: 'absolute', bottom: '100%', right: 0, marginBottom: 4,
              background: RT.panel, border: `1px solid ${RT.borderHi}`,
              borderRadius: 8, padding: 4, zIndex: 20,
              boxShadow: '0 8px 24px rgba(0,0,0,.4)', minWidth: 170,
            }}>
              <button
                style={menuItemStyle}
                onClick={() => { setMenuOpen(false); handleEdit(); }}
                onMouseEnter={(e) => { (e.currentTarget as HTMLButtonElement).style.background = RT.bgRaised; }}
                onMouseLeave={(e) => { (e.currentTarget as HTMLButtonElement).style.background = 'transparent'; }}
              >
                <Icons.terminal size={11} stroke={RT.textDim} /> Edit schedule
              </button>
              {hasOtherDevices && (
                <button
                  style={menuItemStyle}
                  onClick={() => { setMenuOpen(false); setPickerMode('copy'); }}
                  onMouseEnter={(e) => { (e.currentTarget as HTMLButtonElement).style.background = RT.bgRaised; }}
                  onMouseLeave={(e) => { (e.currentTarget as HTMLButtonElement).style.background = 'transparent'; }}
                >
                  <Icons.copy size={11} stroke={RT.textDim} /> Copy to…
                </button>
              )}
              {hasOtherDevices && (
                <button
                  style={menuItemStyle}
                  onClick={() => { setMenuOpen(false); setPickerMode('move'); }}
                  onMouseEnter={(e) => { (e.currentTarget as HTMLButtonElement).style.background = RT.bgRaised; }}
                  onMouseLeave={(e) => { (e.currentTarget as HTMLButtonElement).style.background = 'transparent'; }}
                >
                  <Icons.share size={11} stroke={RT.textDim} /> Move to…
                </button>
              )}
              <div style={{ height: 1, background: RT.border, margin: '3px 0' }} />
              <button
                style={{ ...menuItemStyle, color: RT.red }}
                onClick={() => { setMenuOpen(false); handleDelete(); }}
                onMouseEnter={(e) => { (e.currentTarget as HTMLButtonElement).style.background = RT.bgRaised; }}
                onMouseLeave={(e) => { (e.currentTarget as HTMLButtonElement).style.background = 'transparent'; }}
              >
                <Icons.stop size={11} stroke={RT.red} /> Delete
              </button>
            </div>
          )}
        </div>
      </div>
    </div>

    {/* Device picker overlay for Copy/Move */}
    {pickerMode !== null && (
      <DevicePicker
        cards={cards}
        excludeDeviceId={deviceId}
        title={pickerMode === 'copy' ? 'Copy schedule to…' : 'Move schedule to…'}
        caveat={caveat}
        sourceHomeDir={cards.find((c) => c.id === deviceId)?.home_dir}
        contentSample={`${s.workdir ?? ''} ${s.prompt ?? ''} ${s.instructions_file ?? ''}`}
        onPick={handlePick}
        onClose={() => setPickerMode(null)}
      />
    )}
    </>
  );
}

const menuItemStyle: React.CSSProperties = {
  width: '100%', textAlign: 'left',
  background: 'transparent', border: 'none', borderRadius: 5,
  padding: '8px 11px', cursor: 'pointer', color: RT.text, fontFamily: 'inherit',
  fontSize: 12, display: 'flex', alignItems: 'center', gap: 8,
};
