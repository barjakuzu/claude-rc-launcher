// limitsFormat.ts — formatting and colour helpers for account limits
// (CONTRACT.md section 6). Kept out of tokens.ts because this logic is
// specific to the limits feature, mirroring relativeTime.ts's own file for
// a single shared formatter.
import { RT, capColor } from './tokens';

// Countdown text is derived fresh from the ISO reset timestamp and the
// current tick on every call, never by decrementing a stored duration —
// CONTRACT.md section 6: "must not drift: derive it from the ISO timestamp
// each tick, never by decrementing a stored number." A passed countdown
// reads as "resetting…" rather than a negative duration, and the data is
// treated as stale until the next fetch (isWindowStale below).
//
// Amendment 2: resets_at keeps whatever fractional-second precision the
// API sends, including microseconds ("...511284+00:00"). Date.parse below
// is not a regex and does not care how many fractional digits it sees —
// verified directly (node: `new Date("2026-09-08T00:40:00.511284+00:00")`
// parses to a valid instant, truncated to millisecond resolution, which is
// all a countdown needs). Do not replace this with a stricter parser.
export function formatCountdown(resetsAt: string | null | undefined, nowMs: number): string {
  if (!resetsAt) return '—';
  const resetMs = Date.parse(resetsAt);
  if (Number.isNaN(resetMs)) return '—';
  const diffMs = resetMs - nowMs;
  if (diffMs <= 0) return 'resetting…';
  const totalMin = Math.floor(diffMs / 60_000);
  const h = Math.floor(totalMin / 60);
  const m = totalMin % 60;
  if (h > 0) return `resets in ${h}h ${m}m`;
  if (totalMin < 1) return 'resets in <1m';
  return `resets in ${m}m`;
}

// Absolute local time, shown beneath/on-hover next to the countdown per
// CONTRACT.md section 6.
export function formatAbsolute(resetsAt: string | null | undefined): string {
  if (!resetsAt) return '—';
  const ms = Date.parse(resetsAt);
  if (Number.isNaN(ms)) return '—';
  try {
    return new Date(ms).toLocaleString(undefined, {
      month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
    });
  } catch {
    return '—';
  }
}

export function isWindowStale(resetsAt: string | null | undefined, nowMs: number): boolean {
  if (!resetsAt) return false;
  const resetMs = Date.parse(resetsAt);
  if (Number.isNaN(resetMs)) return false;
  return resetMs - nowMs <= 0;
}

// Colour for one limit reading. "Severity drives colour... never rely on
// the percentage alone, the server's severity is authoritative when
// present" (CONTRACT.md section 6). Amendment 1 (Round 2) gave five_hour
// and seven_day a real severity too, sourced from the matching limits[]
// row and null when none exists — callers now pass it through instead of
// the Round 1 workaround of always passing undefined. The percent-band
// fallback below stays as a safety net for that null case and for a
// backend a step behind the amendment, so the two headline windows still
// read as visually distinct without anyone reading the digits even then.
// An unrecognized severity string (the contract says "assume others
// exist") is treated as urgent rather than calm, since a value neither
// "normal" nor "warning" is not one we can vouch for as fine.
export function limitColor(percent: number, severity?: string | null): string {
  if (percent >= 90) return RT.red;
  if (severity === 'warning') return RT.amber;
  if (severity === 'normal') return RT.green;
  if (severity) return RT.red;
  return capColor(percent);
}
