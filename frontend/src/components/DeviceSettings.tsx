// DeviceSettings.tsx — per-device settings panel (Settings tab).
import { useState } from 'react';
import { RT, FONT_MONO } from '../tokens';
import { Icons } from './primitives';
import { api } from '../api';
import type { DeviceCard } from '../types';

export interface DeviceSettingsProps {
  device: DeviceCard;
  mobile?: boolean;
}

export function DeviceSettings({ device, mobile = false }: DeviceSettingsProps) {
  const [name, setName] = useState(device.name);
  const [pending, setPending] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const dirty = name.trim() !== device.name && name.trim().length > 0;

  const handleSave = async () => {
    if (pending || !dirty) return;
    setError(null);
    setSaved(false);
    setPending(true);
    try {
      const res = await api.deviceRename(device.id, name.trim()) as { ok?: boolean; message?: string };
      if (res && res.ok === false) {
        setError(res.message ?? 'Rename failed.');
      } else {
        setSaved(true);
      }
    } catch {
      setError('Network error — could not reach hub.');
    } finally {
      setPending(false);
    }
  };

  return (
    <div style={{
      background: RT.card, border: `1px solid ${RT.border}`,
      borderRadius: 10, padding: mobile ? 14 : 18,
      display: 'flex', flexDirection: 'column', gap: 14,
      maxWidth: 520,
    }}>
      <div style={{
        fontSize: 10, color: RT.textLow, letterSpacing: '.14em',
        textTransform: 'uppercase', fontFamily: FONT_MONO,
      }}>
        Device settings
      </div>

      {/* Device name */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
        <label style={{ fontSize: 12, color: RT.textDim }}>Device name</label>
        <div style={{ display: 'flex', gap: 8, flexWrap: mobile ? 'wrap' : 'nowrap' }}>
          <input
            value={name}
            onChange={(e) => { setName(e.target.value); setSaved(false); }}
            onKeyDown={(e) => { if (e.key === 'Enter') handleSave(); }}
            maxLength={60}
            placeholder={device.name}
            style={{
              flex: mobile ? '1 1 100%' : 1,
              background: RT.panel, color: RT.text,
              border: `1px solid ${RT.border}`, borderRadius: 7,
              padding: '8px 11px', fontFamily: FONT_MONO, fontSize: 12.5, outline: 'none',
            }}
          />
          <button
            onClick={handleSave}
            disabled={pending || !dirty}
            style={{
              background: dirty && !pending ? RT.text : RT.panel,
              color: dirty && !pending ? RT.bg : RT.textLow,
              border: `1px solid ${dirty && !pending ? RT.text : RT.border}`,
              borderRadius: 7, padding: '8px 16px',
              cursor: dirty && !pending ? 'pointer' : 'default',
              fontFamily: 'inherit', fontSize: 12, fontWeight: 600,
              display: 'inline-flex', alignItems: 'center', gap: 6,
              flex: mobile ? '1 1 100%' : 'none', justifyContent: 'center',
              transition: 'background .15s, color .15s',
            }}
          >
            {pending
              ? <><Icons.spinner size={11} stroke={RT.textLow} /> Saving…</>
              : saved ? '✓ Saved' : 'Save'}
          </button>
        </div>
        <div style={{ fontSize: 11, color: RT.textLow, fontFamily: FONT_MONO, lineHeight: 1.5 }}>
          {device.id === 'local'
            ? 'Shown across the hub UI. Stored on this machine.'
            : 'Shown across the hub UI. Stored in the hub’s device registry.'}
        </div>
      </div>

      {error && (
        <div style={{ fontSize: 11, color: RT.red, fontFamily: FONT_MONO }}>{error}</div>
      )}
    </div>
  );
}
