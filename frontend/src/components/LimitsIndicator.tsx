// LimitsIndicator.tsx — header badge + dropdown for GET /api/limits
// (CONTRACT.md sections 3/5/6). Mirrors AlertsIndicator.tsx's shape
// (self-contained poll via useLimits, dropdown anchored under the button,
// same Z-layer, same "never invent zeros" discipline) with one deliberate
// difference: Alerts hides entirely when clean, because a hidden badge
// there means "nothing to worry about." Limits is the opposite kind of
// indicator — the user asked for it to be visible "like the status bar,"
// so it never disappears once we've heard from the backend at least once;
// it only ever switches between a real reading and a plainly-marked
// unavailable state.
import { useEffect, useRef, useState } from 'react';
import { RT, FONT_MONO, FONT_SANS, Z, withAlpha, fmtPct } from '../tokens';
import { Icons } from './primitives';
import { btn } from './btn';
import { useLimits } from '../hooks/useLimits';
import { useNow } from '../hooks/useNow';
import { formatCountdown, formatAbsolute, isWindowStale, limitColor } from '../limitsFormat';
import { formatRelativeTime } from '../relativeTime';
import type { Layout } from '../useLayout';
import type { LimitsWindow, LimitsScoped, LimitsPrimary, LimitsReport } from '../api';

const STALE_AGE_SECONDS = 120;

// ─── Compact badge: two mini bars, no digits required to read them ─────────

function MiniBar({ pct, color }: { pct: number | null; color: string }) {
  return (
    <div style={{ height: 3, width: 22, borderRadius: 3, background: 'rgba(255,255,255,.10)', overflow: 'hidden', flex: 'none' }}>
      {pct != null && (
        <div style={{
          height: '100%', width: `${Math.min(100, Math.max(4, pct))}%`,
          background: color, borderRadius: 3,
        }} />
      )}
    </div>
  );
}

function BadgeContent({ primary }: { primary: LimitsPrimary }) {
  const fh = primary.five_hour;
  const sd = primary.seven_day;
  const fhColor = fh ? limitColor(fh.percent, fh.severity) : RT.textLow;
  const sdColor = sd ? limitColor(sd.percent, sd.severity) : RT.textLow;
  const urgent = (fh != null && fh.percent >= 90) || (sd != null && sd.percent >= 90);
  return (
    <>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
        <MiniBar pct={fh?.percent ?? null} color={fhColor} />
        <MiniBar pct={sd?.percent ?? null} color={sdColor} />
      </div>
      {urgent && (
        <span style={{
          position: 'absolute', top: -3, right: -3,
          width: 8, height: 8, borderRadius: 8, background: RT.red,
          border: `1.5px solid ${RT.bgRaised}`,
        }} />
      )}
    </>
  );
}

// ─── Dropdown rows ───────────────────────────────────────────────────────

function WindowRow({ label, window, now }: { label: string; window: LimitsWindow | null; now: number }) {
  if (!window) {
    return (
      <div style={{ padding: '8px 9px' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
          <span style={{ fontSize: 12.5, color: RT.text }}>{label}</span>
          <span style={{ fontFamily: FONT_MONO, fontSize: 12, color: RT.textLow }}>—</span>
        </div>
        <div style={{ fontFamily: FONT_MONO, fontSize: 10, color: RT.textLow, marginTop: 4 }}>not reported</div>
      </div>
    );
  }
  const color = limitColor(window.percent, window.severity);
  const stale = isWindowStale(window.resets_at, now);
  const countdown = stale ? 'resetting…' : formatCountdown(window.resets_at, now);
  return (
    <div style={{ padding: '8px 9px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
        <span style={{ fontSize: 12.5, color: RT.text, fontWeight: 500 }}>{label}</span>
        <span style={{ fontFamily: FONT_MONO, fontSize: 13, fontWeight: 600, color }}>{fmtPct(window.percent)}</span>
      </div>
      <div style={{ height: 4, borderRadius: 4, background: 'rgba(255,255,255,.06)', overflow: 'hidden', marginTop: 6 }}>
        <div style={{ height: '100%', width: `${Math.min(100, window.percent)}%`, background: color, borderRadius: 4, transition: 'width .3s ease' }} />
      </div>
      <div
        title={formatAbsolute(window.resets_at)}
        style={{ fontFamily: FONT_MONO, fontSize: 10, color: stale ? RT.amber : RT.textLow, marginTop: 5 }}
      >
        {countdown}
      </div>
    </div>
  );
}

function ScopedRow({ item, now }: { item: LimitsScoped; now: number }) {
  const color = limitColor(item.percent, item.severity);
  const stale = isWindowStale(item.resets_at, now);
  const label = item.label ?? 'Scoped limit';
  return (
    <div style={{ padding: '6px 9px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
        <span style={{ fontSize: 11.5, color: RT.textDim }}>{label}</span>
        <span style={{ fontFamily: FONT_MONO, fontSize: 11.5, color }}>{fmtPct(item.percent)}</span>
      </div>
      <div style={{ height: 3, borderRadius: 3, background: 'rgba(255,255,255,.06)', overflow: 'hidden', marginTop: 4 }}>
        <div style={{ height: '100%', width: `${Math.min(100, item.percent)}%`, background: color, borderRadius: 3 }} />
      </div>
      <div title={formatAbsolute(item.resets_at)} style={{ fontFamily: FONT_MONO, fontSize: 9.5, color: stale ? RT.amber : RT.textLow, marginTop: 3 }}>
        {stale ? 'resetting…' : formatCountdown(item.resets_at, now)}
      </div>
    </div>
  );
}

function fmtMoney(minor: number, currency: string, exponent: number): string {
  const value = minor / Math.pow(10, exponent);
  try {
    return new Intl.NumberFormat(undefined, { style: 'currency', currency }).format(value);
  } catch {
    return `${value.toFixed(exponent)} ${currency}`;
  }
}

function SpendRow({ primary }: { primary: LimitsPrimary }) {
  const spend = primary.spend;
  if (!spend) return null;
  const color = limitColor(spend.percent, spend.severity);
  const used = fmtMoney(spend.used_minor, spend.currency, spend.exponent);
  const limit = spend.limit_minor != null ? fmtMoney(spend.limit_minor, spend.currency, spend.exponent) : null;
  return (
    <div style={{ padding: '6px 9px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
        <span style={{ fontSize: 11.5, color: RT.textDim }}>Spend</span>
        <span style={{ fontFamily: FONT_MONO, fontSize: 11.5, color }}>
          {used}{limit ? ` / ${limit}` : ''}
        </span>
      </div>
      {limit && (
        <div style={{ height: 3, borderRadius: 3, background: 'rgba(255,255,255,.06)', overflow: 'hidden', marginTop: 4 }}>
          <div style={{ height: '100%', width: `${Math.min(100, spend.percent)}%`, background: color, borderRadius: 3 }} />
        </div>
      )}
      {primary.extra_usage?.spend_limit_reached && (
        <div style={{ fontFamily: FONT_MONO, fontSize: 9.5, color: RT.red, marginTop: 3 }}>
          spend limit reached
        </div>
      )}
    </div>
  );
}

function LimitsDetail({ report, now }: { report: LimitsReport; now: number }) {
  const primary = report.primary;
  if (!primary || !primary.available) {
    return (
      <div style={{ padding: '10px 9px', fontFamily: FONT_MONO, fontSize: 12, color: RT.textLow }}>
        {primary && !primary.available
          ? 'No device is currently logged in to Claude, so account limits can’t be read.'
          : 'Limits not available yet.'}
      </div>
    );
  }

  const deviceRow = report.devices.find((d) => d.device_id === primary.device_id);
  const stale = deviceRow?.age_seconds != null && deviceRow.age_seconds > STALE_AGE_SECONDS;
  const staleSince = stale && deviceRow?.age_seconds != null
    ? formatRelativeTime(report.generated_at - deviceRow.age_seconds)
    : null;

  const scoped = primary.scoped ?? [];
  const showSpend = primary.extra_usage?.enabled === true;

  return (
    <>
      {report.divergent && (
        <div style={{
          margin: '0 4px 6px', padding: '8px 9px', borderRadius: 6,
          background: withAlpha(RT.amber, 0.12), border: `1px solid ${withAlpha(RT.amber, 0.4)}`,
          fontSize: 11, color: RT.amber, lineHeight: 1.4,
        }}>
          Devices disagree on usage by more than a few points. Showing the freshest reading.
        </div>
      )}
      {staleSince && (
        <div style={{
          margin: '0 4px 6px', padding: '7px 9px', borderRadius: 6,
          background: withAlpha(RT.amber, 0.10), border: `1px solid ${withAlpha(RT.amber, 0.35)}`,
          fontSize: 10.5, color: RT.amber, lineHeight: 1.4,
        }}>
          This reading is from {staleSince}, not live.
        </div>
      )}

      <WindowRow label="5 hour" window={primary.five_hour} now={now} />
      <WindowRow label="7 day" window={primary.seven_day} now={now} />

      {scoped.length > 0 && (
        <>
          <div style={{ height: 1, background: RT.border, margin: '4px 6px' }} />
          <div style={{
            padding: '4px 9px 2px', fontSize: 9.5, color: RT.textLow,
            letterSpacing: '.1em', textTransform: 'uppercase', fontFamily: FONT_MONO,
          }}>
            Per-model
          </div>
          {scoped.map((s, i) => (
            <ScopedRow key={`${s.kind}:${s.label ?? i}`} item={s} now={now} />
          ))}
        </>
      )}

      {showSpend && primary.spend && (
        <>
          <div style={{ height: 1, background: RT.border, margin: '4px 6px' }} />
          <SpendRow primary={primary} />
        </>
      )}
    </>
  );
}

// ─── Main component ─────────────────────────────────────────────────────

export function LimitsIndicator({ layout }: { layout: Layout }) {
  const { report, status } = useLimits();
  const now = useNow(30_000);
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const off = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    if (open) document.addEventListener('mousedown', off);
    return () => document.removeEventListener('mousedown', off);
  }, [open]);

  // Nothing to show yet (first poll still in flight): no flash of an empty
  // or wrong-shaped badge, mirrors AlertsIndicator. Once we know the route
  // is down, or that no device has data, that must show, not hide — a
  // hidden limits badge reads as "plenty of runway," exactly backwards.
  if (status === 'loading') return null;

  const unavailable = status === 'unavailable' || !report || !report.primary || !report.primary.available;
  const primary = report?.primary ?? null;
  const size = layout.mobile ? 40 : 32;

  const title = unavailable ? 'Account limits unavailable' : 'Account limits';

  return (
    <div ref={ref} style={{ position: 'relative' }}>
      <button
        onClick={() => setOpen((o) => !o)}
        title={title}
        style={{
          ...btn('icon'),
          width: size, height: size,
          position: 'relative',
          borderColor: unavailable ? RT.border : withAlpha(RT.textDim, 0.3),
        }}
      >
        {unavailable ? (
          <Icons.chart size={14} stroke={RT.textLow} />
        ) : (
          <BadgeContent primary={primary as LimitsPrimary} />
        )}
      </button>

      {open && (
        <div style={{
          position: 'absolute', top: '100%', right: 0, marginTop: 6,
          background: RT.panel, border: `1px solid ${RT.borderHi}`,
          borderRadius: 10, width: 300, maxWidth: 'calc(100vw - 28px)',
          maxHeight: 460, overflow: 'auto', padding: 6,
          zIndex: Z.sticky, boxShadow: '0 12px 36px rgba(0,0,0,.4)',
          fontFamily: FONT_SANS,
        }}>
          <div style={{
            padding: '6px 9px 8px', fontSize: 10, color: RT.textLow,
            letterSpacing: '.14em', textTransform: 'uppercase', fontFamily: FONT_MONO,
            display: 'flex', justifyContent: 'space-between', gap: 8,
          }}>
            <span>Limits</span>
            {report && <span>updated {formatRelativeTime(report.generated_at)}</span>}
          </div>

          {unavailable && !report && (
            <div style={{ padding: '10px 9px', fontFamily: FONT_MONO, fontSize: 12, color: RT.textLow }}>
              Limits not available yet.
            </div>
          )}

          {report && <LimitsDetail report={report} now={now} />}
        </div>
      )}
    </div>
  );
}
