// limitsFormat.ts: formatting and colour helpers for account limits
// (CONTRACT.md section 6). Kept out of tokens.ts because this logic is
// specific to the limits feature, mirroring relativeTime.ts's own file for
// a single shared formatter.
import { RT, capColor } from './tokens';

// Countdown text is derived fresh from the ISO reset timestamp and the
// current tick on every call, never by decrementing a stored duration.
// CONTRACT.md section 6: "must not drift: derive it from the ISO timestamp
// each tick, never by decrementing a stored number." A passed countdown
// reads as "resetting…" rather than a negative duration, and the data is
// treated as stale until the next fetch (isWindowStale below).
//
// Amendment 2: resets_at keeps whatever fractional-second precision the
// API sends, including microseconds ("...511284+00:00"). Date.parse below
// is not a regex and does not care how many fractional digits it sees:
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

// Round 4, Critical 1: how old a limits reading actually is right now, not
// how old it was the moment the hub last generated a report. The server's
// own `age_seconds` is a snapshot baked into that report; if the poll has
// been failing since then, the hook (useLimits.ts) keeps serving that same
// frozen report with status 'ok' forever, so age_seconds itself never
// grows and a dead endpoint leaves confident-looking numbers on screen
// with no signal anything is wrong. `lastOkMs` is the client clock at the
// last successful fetch, the one clock that keeps advancing regardless of
// what the server last said, so adding how much client time has passed
// since then to the server's own snapshot age gives the true current age.
// Both inputs are required: either being unknown means the age itself is
// unknown, which callers must treat as "cannot vouch for this", not as
// fresh.
export function deriveAgeSeconds(
  baseAgeSeconds: number | null | undefined,
  lastOkMs: number | null,
  nowMs: number,
): number | null {
  if (baseAgeSeconds == null || lastOkMs == null) return null;
  return baseAgeSeconds + Math.max(0, (nowMs - lastOkMs) / 1000);
}

export const STALE_READING_AGE_SECONDS = 120;

// True only when the derived age is both known and past the threshold.
// An unknown age (null) is deliberately NOT stale here: that is a
// different fact (we cannot vouch for freshness at all) from a known-old
// one, and the two must not collapse into the same visual state.
export function isReadingStale(ageSeconds: number | null): boolean {
  return ageSeconds != null && ageSeconds > STALE_READING_AGE_SECONDS;
}

// Colour for one limit reading. "Severity drives colour... never rely on
// the percentage alone, the server's severity is authoritative when
// present" (CONTRACT.md section 6). Amendment 1 (Round 2) gave five_hour
// and seven_day a real severity too, sourced from the matching limits[]
// row and null when none exists. Callers now pass it through instead of
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
