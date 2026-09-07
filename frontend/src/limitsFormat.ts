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
// present" (CONTRACT.md section 6), but section 3's five_hour/seven_day
// windows carry no severity field at all in the documented shape (only
// scoped[] and spend do) — see task-l2-report.md. When severity is absent
// this falls back to the same percent bands the rest of the app already
// uses for capacity (tokens.ts's capColor), so the two headline windows
// still read as visually distinct without anyone reading the digits. An
// unrecognized severity string (the contract says "assume others exist")
// is treated as urgent rather than calm, since a value neither "normal"
// nor "warning" is not one we can vouch for as fine.
export function limitColor(percent: number, severity?: string | null): string {
  if (percent >= 90) return RT.red;
  if (severity === 'warning') return RT.amber;
  if (severity === 'normal') return RT.green;
  if (severity) return RT.red;
  return capColor(percent);
}
