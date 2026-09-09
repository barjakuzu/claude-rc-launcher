// AlertsIndicator.tsx: header badge + dropdown for GET /api/alerts (Phase 3
// wiring, CONTRACT.md sections 3/5). Red when any alert-severity finding is
// live, amber when only warn-severity findings are live, hidden entirely
// only when the endpoint is confirmed clean.
//
// Unknown must never render as good news: when the route has never once
// responded, the badge shows a grey dot titled "Alerts unavailable" rather
// than disappearing, because a hidden badge here reads as "nothing to
// worry about," which is exactly backwards for a safety indicator. The
// same grey-dot treatment covers a broken guard.json (config_error) with
// zero live findings, since that's a broken config silently falling back
// to defaults, the other quiet failure Phase 3 exists to remove.
import { useEffect, useRef, useState } from 'react';
import { RT, FONT_MONO, FONT_SANS, Z, withAlpha } from '../tokens';
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

export function AlertsIndicator({ mobile = false }: { mobile?: boolean }) {
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
  // empty or wrong-colored badge. This is the only case that renders
  // nothing purely because we haven't heard back yet; once we know the
  // route is down (status === 'unavailable') that must show, not hide.
  if (status === 'loading') return null;

  const unavailable = status === 'unavailable';
  const configError = report?.config_error ?? null;
  const findings = report?.alerts ?? [];
  const summaryAlert = report?.summary.alert ?? 0;
  const summaryWarn = report?.summary.warn ?? 0;
  // The summary is a convenience, not the sole source of truth: a live
  // alert-severity finding must never render as "0 warnings" just because
  // the summary disagreed with the array it's supposed to summarize. Take
  // whichever is higher rather than trusting the summary blindly.
  const alertCount = Math.max(summaryAlert, findings.filter((f) => f.severity === 'alert').length);
  const warnCount = Math.max(summaryWarn, findings.filter((f) => f.severity === 'warn').length);
  // A non-empty alerts[] with (inconsistently) zero summary counts must
  // still show: same reasoning as the count fallback above.
  const hasFindings = alertCount > 0 || warnCount > 0 || findings.length > 0;

  // Hidden entirely only when we positively know the route is healthy and
  // clean. Unavailable is never folded into this branch: "we don't know"
  // must look different from "we checked and it's fine".
  if (!unavailable && !hasFindings && !configError) return null;

  // A broken guard.json and an unreachable endpoint are different problems
  // and must not look the same: unavailable is always textLow (grey),
  // never amber, even when configError alone (no live findings) is what's
  // driving visibility here.
  const color = unavailable
    ? RT.textLow
    : alertCount > 0
      ? RT.red
      : (warnCount > 0 || !!configError)
        ? RT.amber
        : RT.textLow;
  const count = unavailable ? 0 : alertCount > 0 ? alertCount : warnCount;
  const showDot = !unavailable && count === 0 && !!configError;
  const title = unavailable
    ? 'Alerts unavailable'
    : configError && !hasFindings
      ? 'Guard config error'
      : `${count} ${alertCount > 0 ? 'alert' : 'warning'}${count === 1 ? '' : 's'}`;

  return (
    <div ref={ref} style={{ position: 'relative' }}>
      <button
        onClick={() => setOpen((o) => !o)}
        title={title}
        style={{
          ...btn('icon'),
          // 44px mobile floor (Apple HIG / Material), same as the Share
          // tunnel button beside this one in the header.
          width: mobile ? 44 : 32,
          height: mobile ? 44 : 32,
          position: 'relative',
          borderColor: (hasFindings || configError || unavailable) ? withAlpha(color, 0.4) : RT.border,
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
        {(unavailable || showDot) && (
          <span style={{
            position: 'absolute', top: -3, right: -3,
            width: 8, height: 8, borderRadius: 8, background: color,
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
          WebkitOverflowScrolling: 'touch', overscrollBehavior: 'contain',
          zIndex: Z.sticky, boxShadow: '0 12px 36px rgba(0,0,0,.4)',
          fontFamily: FONT_SANS,
        }}>
          <div style={{
            padding: '6px 9px 8px', fontSize: 10, color: RT.textLow,
            letterSpacing: '.14em', textTransform: 'uppercase', fontFamily: FONT_MONO,
            display: 'flex', justifyContent: 'space-between', gap: 8,
          }}>
            <span>Alerts</span>
            {/* A permanently failing route must not keep showing the last
                good numbers as though they were live: the age makes a
                stale reading visibly stale instead of silently current. */}
            {report && <span>updated {formatRelativeTime(report.generated_at)}</span>}
          </div>

          {unavailable && (
            <div style={{ padding: '10px 9px', fontFamily: FONT_MONO, fontSize: 12, color: RT.textLow }}>
              Alerts not available yet.
            </div>
          )}

          {!unavailable && configError && (
            <div style={{
              margin: '0 4px 6px', padding: '8px 9px', borderRadius: 6,
              background: withAlpha(RT.amber, 0.12), border: `1px solid ${withAlpha(RT.amber, 0.4)}`,
              fontSize: 11.5, color: RT.amber, lineHeight: 1.4,
            }}>
              guard.json failed to load: {configError}. Thresholds below may be defaults, not what is configured.
            </div>
          )}

          {!unavailable && findings.length === 0 && (
            <div style={{ padding: '10px 9px', fontFamily: FONT_MONO, fontSize: 12, color: RT.textLow }}>
              No live findings.
            </div>
          )}

          {!unavailable && findings.map((f) => (
            <AlertRow key={`${f.device_id}:${f.session_id}:${f.rule}`} f={f} />
          ))}
        </div>
      )}
    </div>
  );
}
