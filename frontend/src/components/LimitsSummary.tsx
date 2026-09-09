// LimitsSummary.tsx: always-visible account-limits summary for the top
// strip (Round 3 of the limits/mobile lane). Round 1/2 put this behind a
// small header icon that opened a dropdown reading "Limits not available
// yet", invisible in a screenshot and the opposite of "visible like the
// status bar" (the user's own words). This replaces that badge: both
// windows, their percentages, their reset countdowns and their severity
// colour are drawn directly in the strip, no tap required. The dropdown
// this opens on tap is for the things that genuinely need drill-down:
// scoped per-model limits, spend, staleness, cross-device divergence,
// not for the headline numbers themselves.
//
// Unlike the badge it replaces, this component is a permanent part of the
// strip's layout (Strip.tsx / the mobile top strip), not something that
// can pop in and out. So unlike AlertsIndicator's "return null while
// loading" convention, this always renders its cells, showing '—'
// placeholders until real data arrives rather than collapsing the row.
//
// Round 4: knowing the number is half of "in control"; knowing whether to
// trust it is the other half. Staleness and divergence used to live only
// in the dropdown, which defeats the point of moving the numbers into the
// always-visible strip in the first place. Both now show directly on the
// cells: a dimmed, dotted cell when the reading itself is old (derived
// live age, not the frozen snapshot the hub last reported), and a small
// marker on the whole summary when devices disagree.
import { useEffect, useRef, useState } from 'react';
import { RT, FONT_MONO, FONT_SANS, Z, withAlpha, fmtPct } from '../tokens';
import { useLimits } from '../hooks/useLimits';
import { useNow } from '../hooks/useNow';
import {
  formatCountdown, formatAbsolute, isWindowStale, limitColor,
  deriveAgeSeconds, isReadingStale,
} from '../limitsFormat';
import { formatRelativeTime } from '../relativeTime';
import { Icons } from './primitives';
import type { LimitsWindow, LimitsScoped, LimitsPrimary, LimitsReport } from '../api';

// ─── Compact cell: the always-visible headline reading ──────────────────

function StaleDot({ ageSeconds }: { ageSeconds: number | null }) {
  const label = ageSeconds != null
    ? `Reading is from ${formatRelativeTime(Date.now() / 1000 - ageSeconds)}, not live`
    : 'Reading age unknown';
  return (
    <span
      title={label}
      style={{
        width: 5, height: 5, borderRadius: 5, background: RT.amber, flex: 'none',
        display: 'inline-block',
      }}
    />
  );
}

function WindowCell({ label, window, now, compact, loading, dataStale, ageSeconds }: {
  label: string; window: LimitsWindow | null; now: number; compact: boolean;
  loading: boolean; dataStale: boolean; ageSeconds: number | null;
}) {
  const has = window != null;
  const color = has ? limitColor(window.percent, window.severity) : RT.textLow;
  const countdownPassed = has && isWindowStale(window.resets_at, now);
  const countdown = !has
    ? (loading ? 'loading…' : 'no data')
    : countdownPassed ? 'resetting…' : formatCountdown(window.resets_at, now);
  return (
    <div style={{ minWidth: 0, flex: compact ? 1 : 'none', opacity: dataStale ? 0.55 : 1 }}>
      <div style={{
        display: 'flex', alignItems: 'center', gap: 5,
        fontSize: compact ? 9 : 10, color: RT.textLow, letterSpacing: '.14em',
        textTransform: 'uppercase', fontFamily: FONT_MONO, marginBottom: compact ? 3 : 6,
      }}>
        {label}
        {dataStale && <StaleDot ageSeconds={ageSeconds} />}
      </div>
      <div style={{
        fontSize: compact ? 16 : 20, fontWeight: 600, fontFamily: FONT_MONO,
        color, lineHeight: 1, letterSpacing: '-.01em',
      }}>
        {has ? fmtPct(window.percent) : '—'}
      </div>
      <div style={{
        height: 3, borderRadius: 3, background: 'rgba(255,255,255,.08)',
        overflow: 'hidden', marginTop: compact ? 5 : 7, maxWidth: compact ? undefined : 130,
      }}>
        {has && (
          <div style={{ height: '100%', width: `${Math.min(100, window.percent)}%`, background: color, borderRadius: 3 }} />
        )}
      </div>
      <div style={{
        fontFamily: FONT_MONO, fontSize: compact ? 9 : 10.5,
        color: countdownPassed ? RT.amber : RT.textLow, marginTop: compact ? 4 : 5,
        whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
      }}>
        {countdown}
      </div>
    </div>
  );
}

// Round 8 (mobile-shell v4): the mobile strip used to repeat WindowCell's
// full two-column block (label + big percent + bar + countdown, twice),
// which alone ran ~80-90px tall and, combined with the rest of the strip,
// covered roughly a third of a 390-wide phone screen above every tab. On
// a phone, the countdown and the bar are drill-down detail, not headline
// information -- the number is. LineChip is that headline alone, sized to
// sit on one line: label, percent, done. Everything WindowCell drew below
// it (bar, countdown, staleness prose) still lives one tap away in the
// unchanged LimitsDetail dropdown below.
function LineChip({ label, window, color: colorOverride }: {
  label: string; window: LimitsWindow | null; color?: string;
}) {
  const has = window != null;
  const color = colorOverride ?? (has ? limitColor(window.percent, window.severity) : RT.textLow);
  return (
    <span style={{ display: 'inline-flex', alignItems: 'baseline', gap: 4, minWidth: 0 }}>
      <span style={{
        fontFamily: FONT_MONO, fontSize: 10, color: RT.textLow,
        letterSpacing: '.1em', textTransform: 'uppercase',
      }}>{label}</span>
      <span style={{ fontFamily: FONT_MONO, fontSize: 13.5, fontWeight: 600, color }}>
        {has ? fmtPct(window.percent) : '—'}
      </span>
    </span>
  );
}

// ─── Dropdown detail: scoped limits, spend, staleness, divergence ───────

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

// Exponent is clamped defensively even though isLimitsSpend (api.ts) now
// validates it is an integer in 0-20: a second guard here costs nothing
// and this is the one place an out-of-range value would otherwise throw
// past render into the root error boundary and take the whole app down.
function fmtMoney(minor: number, currency: string, exponent: number): string {
  const safeExponent = Number.isInteger(exponent) ? Math.min(20, Math.max(0, exponent)) : 2;
  const value = minor / Math.pow(10, safeExponent);
  try {
    return new Intl.NumberFormat(undefined, { style: 'currency', currency }).format(value);
  } catch {
    return `${value.toFixed(safeExponent)} ${currency}`;
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

function LimitsDetail({ report, now, ageSeconds, dataStale }: {
  report: LimitsReport; now: number; ageSeconds: number | null; dataStale: boolean;
}) {
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

  // Same derived age the always-visible cells use (see LimitsSummary
  // below): a fixed "as of" instant computed from the age at last success
  // plus how much client time has passed since, not the frozen snapshot
  // the hub last reported. formatRelativeTime re-derives "ago" live from
  // Date.now() on every render, so this stays correct as time passes.
  const staleSince = dataStale && ageSeconds != null
    ? formatRelativeTime(Date.now() / 1000 - ageSeconds)
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

// ─── Main component ──────────────────────────────────────────────────────

export function LimitsSummary({ mobile }: { mobile: boolean }) {
  const { report, status, lastOkMs } = useLimits();
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

  const primary = report?.primary ?? null;
  const unavailable = status === 'unavailable' || (status === 'ok' && (!report || !primary || !primary.available));
  const fh = primary?.available ? primary.five_hour : null;
  const sd = primary?.available ? primary.seven_day : null;

  // Round 4, Critical 1: the hub's own age_seconds is a snapshot from the
  // last successful fetch and never grows once the poll starts failing
  // (useLimits.ts keeps status 'ok' and the last report forever). Adding
  // how much client time has passed since that last success
  // (deriveAgeSeconds) is what makes the reading visibly age on screen
  // instead of sitting there looking exactly as fresh as it was minutes
  // or hours ago.
  const deviceRow = report && primary
    ? report.devices.find((d) => d.device_id === primary.device_id)
    : undefined;
  const ageSeconds = deriveAgeSeconds(deviceRow?.age_seconds, lastOkMs, now);
  const dataStale = isReadingStale(ageSeconds);
  const divergent = report?.divergent === true;

  const title = unavailable
    ? 'Account limits unavailable, tap for detail'
    : dataStale ? 'Account limits, reading is not live, tap for detail'
    : 'Account limits, tap for detail';

  return (
    <div ref={ref} style={{ position: 'relative', flex: mobile ? 'none' : 1, minWidth: 0, height: mobile ? 'auto' : '100%' }}>
      <button
        onClick={() => setOpen((o) => !o)}
        title={title}
        style={mobile ? {
          background: 'transparent', border: 'none', padding: 0, margin: 0,
          cursor: 'pointer', color: 'inherit', font: 'inherit', textAlign: 'left',
          display: 'flex', width: '100%', alignItems: 'center', gap: 10,
        } : {
          background: 'transparent', border: 'none', padding: 0, margin: 0,
          cursor: 'pointer', color: 'inherit', font: 'inherit', textAlign: 'left',
          display: 'flex', width: '100%', height: '100%',
          gap: 24, alignItems: 'flex-start', position: 'relative',
        }}
      >
        {mobile ? (
          <>
            <LineChip label="5H" window={fh} />
            <div style={{ width: 1, height: 12, background: RT.border, flex: 'none' }} />
            <LineChip label="7D" window={sd} />
            {dataStale && <StaleDot ageSeconds={ageSeconds} />}
            {divergent && (
              <span
                title="Devices disagree on usage. Showing the freshest reading."
                style={{ width: 5, height: 5, borderRadius: 5, background: RT.amber, flex: 'none' }}
              />
            )}
            <span style={{ flex: 1 }} />
            {/* Visible tap-to-expand affordance -- the title attribute
                alone is a hover tooltip, invisible and useless on touch,
                so this is the one thing on the line that actually tells a
                phone user there's more here. */}
            <Icons.chevDown size={13} stroke={RT.textLow} sw={2} />
          </>
        ) : (
          <>
            <WindowCell label="5 Hour" window={fh} now={now} compact={false} loading={status === 'loading'} dataStale={dataStale} ageSeconds={ageSeconds} />
            <WindowCell label="7 Day" window={sd} now={now} compact={false} loading={status === 'loading'} dataStale={dataStale} ageSeconds={ageSeconds} />
            {divergent && (
              <span
                title="Devices disagree on usage. Showing the freshest reading."
                style={{
                  position: 'absolute', top: -2, right: -2,
                  width: 7, height: 7, borderRadius: 7, background: RT.amber,
                  border: `1.5px solid ${RT.bgRaised}`,
                }}
              />
            )}
          </>
        )}
      </button>

      {open && (
        <div style={{
          position: 'absolute', top: 'calc(100% + 8px)', left: 0,
          background: RT.panel, border: `1px solid ${RT.borderHi}`,
          borderRadius: 10, width: 300, maxWidth: 'calc(100vw - 28px)',
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
            <span>Limits</span>
            {report && <span>updated {formatRelativeTime(report.generated_at)}</span>}
          </div>

          {!report && (
            <div style={{ padding: '10px 9px', fontFamily: FONT_MONO, fontSize: 12, color: RT.textLow }}>
              Limits not available yet.
            </div>
          )}

          {report && <LimitsDetail report={report} now={now} ageSeconds={ageSeconds} dataStale={dataStale} />}
        </div>
      )}
    </div>
  );
}
