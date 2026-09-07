// DeviceSettings.tsx — per-device settings panel (Settings tab).
import { useEffect, useState } from 'react';
import { RT, FONT_MONO, FONT_SANS } from '../tokens';
import { Icons } from './primitives';
import { api, fetchAudit } from '../api';
import type { AuditEntry } from '../api';
import type { DeviceCard } from '../types';
import { ConfigMatrixView } from './ConfigMatrix';

export interface DeviceSettingsProps {
  device: DeviceCard;
  cards: DeviceCard[];
  mobile?: boolean;
}

export function DeviceSettings({ device, cards, mobile = false }: DeviceSettingsProps) {
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

      {/* Config parity */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
        <div style={{
          fontSize: 10, color: RT.textLow, letterSpacing: '.14em',
          textTransform: 'uppercase', fontFamily: FONT_MONO,
        }}>
          Config
        </div>
        <div style={{ margin: '0 -18px' }}>
          <ConfigMatrixView cards={cards} />
        </div>
      </div>

      {/* Audit — last 50 hub audit-log entries (Task 11). Hub-wide, not
          scoped to this device. */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
        <div style={{
          fontSize: 10, color: RT.textLow, letterSpacing: '.14em',
          textTransform: 'uppercase', fontFamily: FONT_MONO,
        }}>
          Audit
        </div>
        <AuditSection />
      </div>
    </div>
  );
}

function formatAuditTime(epochSeconds: number | null | undefined): string {
  if (epochSeconds == null) return '—';
  try {
    return new Date(epochSeconds * 1000).toLocaleString();
  } catch {
    return '—';
  }
}

const auditThStyle = {
  textAlign: 'left' as const, padding: '6px 10px', color: RT.textLow,
  fontSize: 10, letterSpacing: '.06em', textTransform: 'uppercase' as const, fontFamily: FONT_MONO,
};

const auditTdStyle = {
  padding: '6px 10px', fontFamily: FONT_MONO, fontSize: 11.5, color: RT.textDim,
  borderTop: `1px solid ${RT.border}`,
};

function AuditSection() {
  const [audit, setAudit] = useState<AuditEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(false);

  useEffect(() => {
    let cancelled = false;
    fetchAudit(50)
      .then((data) => { if (!cancelled) setAudit(data.audit ?? []); })
      .catch(() => { if (!cancelled) setLoadError(true); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, []);

  if (loadError) {
    return <div style={{ fontSize: 11, color: RT.red, fontFamily: FONT_MONO }}>Failed to load audit log.</div>;
  }
  if (loading) {
    return <div style={{ fontSize: 11, color: RT.textLow, fontFamily: FONT_MONO }}>Loading…</div>;
  }
  if (audit.length === 0) {
    return <div style={{ fontSize: 11, color: RT.textLow, fontFamily: FONT_MONO }}>No audit entries yet.</div>;
  }

  return (
    <div style={{ overflowX: 'auto', margin: '0 -18px', padding: '0 18px' }}>
      <table style={{ borderCollapse: 'collapse', width: '100%', fontFamily: FONT_SANS }}>
        <thead>
          <tr>
            <th style={auditThStyle}>Time</th>
            <th style={auditThStyle}>Actor</th>
            <th style={auditThStyle}>Action</th>
            <th style={auditThStyle}>Target</th>
            <th style={auditThStyle}>Device</th>
            <th style={auditThStyle}>Detail</th>
          </tr>
        </thead>
        <tbody>
          {audit.map((a) => (
            <tr key={a.id}>
              <td style={auditTdStyle}>{formatAuditTime(a.ts)}</td>
              <td style={auditTdStyle}>{a.actor || '—'}</td>
              <td style={auditTdStyle}>{a.action || '—'}</td>
              <td style={auditTdStyle}>{a.target || '—'}</td>
              <td style={auditTdStyle}>{a.device_id || '—'}</td>
              <td style={auditTdStyle}>{a.detail || '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
