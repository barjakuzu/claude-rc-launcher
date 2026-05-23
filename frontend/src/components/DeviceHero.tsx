// DeviceHero.tsx — V5 hero block: icon chip + name + status + meta + Stop all + SSH + 4 stat cells.
import { useState, useRef, useEffect } from 'react';
import { RT, FONT_MONO, tintFor, tintSoft, hueForId, fmtK, kindForOs } from '../tokens';
import { Icons, Dot, CapBar } from './primitives';
import { api } from '../api';
import type { DeviceCard } from '../types';

interface HeroProps {
  device: DeviceCard;
  mobile?: boolean;
  onClose?: () => void;
  onStopAllDone?: () => void;
}

// V5Stat cell — compact stat with optional bar
function V5Stat({
  label, value, sub, bar, barColor, divider, mobile,
}: {
  label: string; value: string | number; sub?: string;
  bar?: number; barColor?: string; divider?: boolean; mobile?: boolean;
}) {
  return (
    <div style={{
      padding: mobile ? 0 : '0 22px',
      borderLeft: !mobile && divider ? `1px solid ${RT.border}` : 'none',
    }}>
      <div style={{
        fontSize: 9, color: RT.textLow, letterSpacing: '.14em',
        textTransform: 'uppercase', fontFamily: FONT_MONO, marginBottom: 5,
      }}>
        {label}
      </div>
      <div style={{
        fontSize: mobile ? 14 : 16, fontWeight: 500,
        letterSpacing: '-.015em', fontFamily: FONT_MONO,
      }}>
        {value}
      </div>
      {bar != null && (
        <div style={{ marginTop: 6 }}>
          <CapBar pct={bar} height={3} bg="rgba(255,255,255,.05)" color={barColor || RT.accent} />
        </div>
      )}
      {sub && (
        <div style={{ fontSize: 10, color: RT.textLow, marginTop: 4, fontFamily: FONT_MONO }}>
          {sub}
        </div>
      )}
    </div>
  );
}

export function DeviceHero({ device, mobile = false, onClose, onStopAllDone }: HeroProps) {
  const hue = hueForId(device.id);
  const hueColor = tintFor(hue, 0.70, 0.10);
  const KindIcon = Icons[kindForOs(device.os)] || Icons.server;

  const [stopPending, setStopPending] = useState(false);
  const mounted = useRef(true);
  useEffect(() => () => { mounted.current = false; }, []);

  const handleStopAll = async () => {
    if (stopPending || !window.confirm(`Stop all sessions on ${device.name}?`)) return;
    setStopPending(true);
    try {
      await api.stopAll(device.id);
    } catch {/* ignore */}
    finally {
      if (mounted.current) {
        setStopPending(false);
        onStopAllDone?.();
      }
    }
  };

  // lastActivity: if loadPct>0 → 'just now', sessions>0 → 'active', else 'idle'
  const lastActivity = device.loadPct > 0 ? 'just now' : device.sessions > 0 ? 'active' : 'idle';

  // CPU load as 0-100
  const cpuPct = device.loadPct;
  const cpuBarColor = cpuPct > 85 ? RT.red : cpuPct > 60 ? RT.amber : hueColor;

  return (
    <div style={{
      flex: 'none',
      padding: mobile ? '14px' : '20px 28px',
      borderBottom: `1px solid ${RT.border}`,
      background: RT.bg,
    }}>
      {/* Name row */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 14, marginBottom: 14 }}>
        {mobile && onClose && (
          <button
            onClick={onClose}
            style={{
              background: RT.panel, border: `1px solid ${RT.border}`, borderRadius: 7,
              width: 30, height: 30, padding: 0, cursor: 'pointer',
              display: 'flex', alignItems: 'center', justifyContent: 'center', flex: 'none',
            }}
          >
            <Icons.back size={14} stroke={RT.textDim} />
          </button>
        )}

        {/* Icon chip */}
        <div style={{
          width: mobile ? 36 : 44, height: mobile ? 36 : 44, borderRadius: 10, flex: 'none',
          background: tintSoft(hue), color: hueColor,
          display: 'flex', alignItems: 'center', justifyContent: 'center',
        }}>
          <KindIcon size={mobile ? 18 : 22} stroke={hueColor} />
        </div>

        {/* Name + status + meta */}
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
            <div style={{ fontSize: mobile ? 17 : 22, fontWeight: 600, letterSpacing: '-.015em' }}>
              {device.name}
            </div>
            <Dot color={device.online ? RT.green : RT.textLow} size={7} pulse={device.online} />
            <span style={{
              fontSize: 10, fontFamily: FONT_MONO,
              color: device.online ? RT.green : RT.textLow,
              letterSpacing: '.06em', textTransform: 'uppercase',
            }}>
              {device.online ? 'online' : 'offline'}
            </span>
          </div>
          <div style={{
            fontSize: 11.5, color: RT.textDim, fontFamily: FONT_MONO,
            marginTop: 4, display: 'flex', gap: 12, flexWrap: 'wrap',
          }}>
            <span>{device.hostname}</span>
            <span style={{ color: RT.borderHi }}>·</span>
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
              <Icons.globe size={11} stroke={RT.textLow} /> {device.os || 'unknown'}
            </span>
            <span style={{ color: RT.borderHi }}>·</span>
            <span>{device.loadPct}% cpu</span>
          </div>
        </div>

        {/* Stop all + SSH (desktop only) */}
        {!mobile && (
          <div style={{ display: 'flex', gap: 8 }}>
            <button
              onClick={handleStopAll}
              disabled={stopPending}
              style={{
                background: RT.panel, border: `1px solid ${RT.border}`, borderRadius: 7,
                padding: '7px 12px', color: stopPending ? RT.textLow : RT.text,
                fontSize: 12, fontWeight: 500, cursor: stopPending ? 'not-allowed' : 'pointer',
                fontFamily: 'inherit', display: 'inline-flex', alignItems: 'center', gap: 6,
                opacity: stopPending ? 0.6 : 1,
              }}
            >
              <Icons.stop size={12} stroke={RT.red} />
              {stopPending ? 'Stopping…' : 'Stop all'}
            </button>
            <button
              disabled
              title="SSH not available"
              style={{
                background: RT.panel, border: `1px solid ${RT.border}`, borderRadius: 7,
                padding: '7px 12px', color: RT.textDim,
                fontSize: 12, fontWeight: 500, cursor: 'not-allowed',
                fontFamily: 'inherit', display: 'inline-flex', alignItems: 'center', gap: 6,
                opacity: 0.4,
              }}
            >
              <Icons.terminal size={12} stroke={RT.textDim} /> SSH
            </button>
          </div>
        )}
      </div>

      {/* 4 stat cells */}
      <div style={{
        display: 'grid',
        gridTemplateColumns: mobile ? '1fr 1fr' : 'repeat(4, 1fr)',
        gap: mobile ? 10 : 0,
      }}>
        <V5Stat
          label="Token usage"
          value={fmtK(device.tokens)}
          bar={device.loadPct}
          barColor={hueColor}
          mobile={mobile}
        />
        <V5Stat
          label="Sessions"
          value={device.sessions}
          sub="active"
          divider={!mobile}
          mobile={mobile}
        />
        <V5Stat
          label="Last activity"
          value={lastActivity}
          divider={!mobile}
          mobile={mobile}
        />
        <V5Stat
          label="CPU load"
          value={`${device.loadPct}%`}
          bar={cpuPct}
          barColor={cpuBarColor}
          divider={!mobile}
          mobile={mobile}
        />
      </div>
    </div>
  );
}
