// AlertsIndicator.tsx: header badge + dropdown for GET /api/alerts (Phase 3
// wiring, CONTRACT.md sections 3/5). Red when any alert-severity finding is
// live, amber when only warn-severity findings are live, hidden entirely
// when clean and the guard config is healthy.
//
// A broken guard.json (config_error) is surfaced even with zero live
// findings. The whole point of Phase 3 is to remove exactly this kind of
// quiet failure, so it gets its own always-visible state rather than being
// swallowed alongside a "0 alerts" reading.
import { useEffect, useRef, useState } from 'react';
import { RT, FONT_MONO, FONT_SANS, Z } from '../tokens';
import { Icons, Dot } from './primitives';
import { btn } from './btn';
import { useAlerts } from '../hooks/useAlerts';
import { formatRelativeTime } from '../relativeTime';
import type { AlertFinding } from '../api';

function severityColor(severity: string): string {
  if (severity === 'alert') return RT.red;
  if (severity === 'warn') return RT.amber;
  return RT.textLow;
}

function AlertRow({ f }: { f: AlertFinding }) {
  const color = severityColor(f.severity);
  const target = f.name || f.session_id || f.device_id;
  return (
    <div style={{
      padding: '9px 11px', borderRadius: 6,
      display: 'flex', flexDirection: 'column', gap: 4,
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
        <Dot color={color} size={6} pulse={f.severity === 'alert'} />
        <span style={{
          fontFamily: FONT_MONO, fontSize: 10, letterSpacing: '.06em',
          textTransform: 'uppercase', color,
        }}>{f.rule}</span>
        <span style={{ fontFamily: FONT_MONO, fontSize: 10, color: RT.textLow }}>
          {f.target_type} · {target}
        </span>
      </div>
      <div style={{ fontSize: 12.5, color: RT.text, lineHeight: 1.4 }}>{f.message}</div>
      <div style={{ fontFamily: FONT_MONO, fontSize: 10, color: RT.textLow }}>
        firing since {formatRelativeTime(f.first_seen)}
      </div>
    </div>
  );
}

export function AlertsIndicator() {
  const { report, status } = useAlerts();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const off = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    if (open) document.addEventListener('mousedown', off);
    return () => document.removeEventListener('mousedown', off);
  }, [open]);

  // Nothing to show yet (first poll still in flight): no flash of an
  // empty or wrong-colored badge.
  if (status === 'loading') return null;

  const alertCount = report?.summary.alert ?? 0;
  const warnCount = report?.summary.warn ?? 0;
  const configError = report?.config_error ?? null;
  const hasFindings = alertCount > 0 || warnCount > 0;

  // Clean and healthy (or the endpoint has never once responded: status is
  // only 'unavailable' when report is still null): render nothing, per
  // spec. No permanent "not available" nag in the header.
  if (!hasFindings && !configError) return null;

  const color = alertCount > 0 ? RT.red : warnCount > 0 ? RT.amber : RT.textLow;
  const count = alertCount > 0 ? alertCount : warnCount;
  const findings = report?.alerts ?? [];

  return (
    <div ref={ref} style={{ position: 'relative' }}>
      <button
        onClick={() => setOpen((o) => !o)}
        title={configError ? 'Guard config error' : `${count} ${alertCount > 0 ? 'alert' : 'warning'}${count === 1 ? '' : 's'}`}
        style={{
          ...btn('icon'),
          position: 'relative',
          borderColor: hasFindings || configError ? `${color}66` : RT.border,
          color,
        }}
      >
        <Icons.alertTriangle size={14} stroke={color} />
        {count > 0 && (
          <span style={{
            position: 'absolute', top: -5, right: -5,
            minWidth: 15, height: 15, padding: '0 3px',
            borderRadius: 8, background: color, color: RT.bg,
            fontFamily: FONT_MONO, fontSize: 9, fontWeight: 700,
            display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
            border: `1.5px solid ${RT.bgRaised}`,
          }}>{count}</span>
        )}
        {!count && configError && (
          <span style={{
            position: 'absolute', top: -3, right: -3,
            width: 8, height: 8, borderRadius: 8, background: RT.amber,
            border: `1.5px solid ${RT.bgRaised}`,
          }} />
        )}
      </button>

      {open && (
        <div style={{
          position: 'absolute', top: '100%', right: 0, marginTop: 6,
          background: RT.panel, border: `1px solid ${RT.borderHi}`,
          borderRadius: 10, width: 340, maxWidth: 'calc(100vw - 28px)',
          maxHeight: 420, overflow: 'auto', padding: 6,
          zIndex: Z.sticky, boxShadow: '0 12px 36px rgba(0,0,0,.4)',
          fontFamily: FONT_SANS,
        }}>
          <div style={{
            padding: '6px 9px 8px', fontSize: 10, color: RT.textLow,
            letterSpacing: '.14em', textTransform: 'uppercase', fontFamily: FONT_MONO,
          }}>
            Alerts
          </div>

          {configError && (
            <div style={{
              margin: '0 4px 6px', padding: '8px 9px', borderRadius: 6,
              background: 'oklch(0.72 0.09 78 / 0.12)', border: `1px solid ${RT.amber}66`,
              fontSize: 11.5, color: RT.amber, lineHeight: 1.4,
            }}>
              guard.json failed to load: {configError}. Thresholds below may be defaults, not what is configured.
            </div>
          )}

          {findings.length === 0 && (
            <div style={{ padding: '10px 9px', fontFamily: FONT_MONO, fontSize: 12, color: RT.textLow }}>
              No live findings.
            </div>
          )}

          {findings.map((f) => (
            <AlertRow key={`${f.device_id}:${f.session_id}:${f.rule}`} f={f} />
          ))}
        </div>
      )}
    </div>
  );
}
