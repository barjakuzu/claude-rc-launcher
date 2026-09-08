// tokens.ts — design tokens and shared helpers.

// Z-index scale — single source of truth for stacking. Higher layers must
// always beat lower ones: raised < sticky < menu < sheet < modal < picker.
export const Z = {
  raised: 1,    // active pill in a segmented control
  sticky: 30,   // sticky headers / toolbars
  menu: 60,     // row-action popover menus (must beat sticky bars)
  sheet: 70,    // mobile bottom sheets (backdrop 70, panel 71)
  modal: 80,    // full-screen modal overlays
  picker: 100,  // device picker (may sit on top of a modal)
} as const;

export const FONT_SANS = "'Inter', system-ui, sans-serif";
export const FONT_SERIF = "'Instrument Serif', Georgia, serif";
export const FONT_MONO = "'Geist Mono', ui-monospace, SFMono-Regular, monospace";

// Refined dark palette — quieter, more professional. (variant-ops-refined.jsx)
export const RT = {
  bg: 'oklch(0.155 0.004 80)',
  bgRaised: 'oklch(0.195 0.006 80)',
  panel: 'oklch(0.215 0.006 80)',
  card: 'oklch(0.225 0.006 80)',
  cardHi: 'oklch(0.255 0.008 80)',
  border: 'oklch(0.28 0.007 80)',
  borderHi: 'oklch(0.36 0.009 80)',
  text: 'oklch(0.96 0.004 80)',
  textDim: 'oklch(0.72 0.006 80)',
  textLow: 'oklch(0.52 0.007 80)',
  accent: 'oklch(0.70 0.10 250)', // subtle blue
  green: 'oklch(0.66 0.10 150)',
  amber: 'oklch(0.72 0.09 78)',
  red: 'oklch(0.62 0.12 25)',
} as const;

// Functional palette (tokens.jsx) — used by capColor thresholds.
export const FN = {
  blue: 'oklch(0.66 0.14 250)',
  green: 'oklch(0.70 0.15 150)',
  amber: 'oklch(0.78 0.14 78)',
  red: 'oklch(0.66 0.18 25)',
} as const;

// Low-chroma device tints — almost neutral with a hint of hue.
export const tintFor = (hue: number, L = 0.66, C = 0.07): string => `oklch(${L} ${C} ${hue})`;
export const tintSoft = (hue: number): string => `oklch(0.66 0.07 ${hue} / 0.14)`;
export const tintEdge = (hue: number): string => `oklch(0.66 0.07 ${hue} / 0.32)`;

// Format helpers.
export const fmtK = (n: number): string => {
  // A malformed backend payload (a string, undefined, NaN) must never
  // render as the literal text "NaN"/"undefined"/"null", but it must also
  // never render as "0": zero is a real value, this is a "we can't format
  // this" fallback, so it uses the same unknown-value dash as fmtUsage.
  if (typeof n !== 'number' || !Number.isFinite(n)) return '—';
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(n >= 10_000_000 ? 0 : 1) + 'M';
  if (n >= 1000) return (n / 1000).toFixed(0) + 'K';
  return String(n);
};
export const fmtPct = (n: number): string => Math.round(n) + '%';

// Effective-token usage label for a session/device `usage` field that is
// null when unknown (never a fake 0). Part of Phase 3 wiring's usage
// accounting. Takes the usage object itself (or a stand-in shaped like one)
// so callers can pass `s.usage` directly and get the right tri-state
// behavior:
//   undefined -> null   (field not sent by this backend yet, render nothing)
//   null      -> '—'    (backend confirmed no transcript data exists)
//   object    -> fmtK(usage.effective)
export const fmtUsage = (usage: { effective: number } | null | undefined): string | null => {
  if (usage === undefined) return null;
  if (usage === null) return '—';
  return fmtK(usage.effective);
};

// Tri-state device usage_partial lookup: true (confirmed partial), false
// (confirmed not partial), undefined (unknown, e.g. because /api/fleet
// hasn't reported this device yet, or the field is absent on an older
// backend). Never coerce this with `!!`: that turns "we don't know" into
// "not partial", which is exactly backwards for a marker whose whole job
// is flagging numbers that may be under-reporting.
export function usagePartialFor(
  devices: { id: string; usage_partial?: boolean }[],
  deviceId: string,
): boolean | undefined {
  return devices.find((d) => d.id === deviceId)?.usage_partial;
}

// Live per-device effective-token total, summed from /api/fleet's
// per-session `usage.effective` (Round 2: BigCard.tsx/DeviceHero.tsx used
// to render the old TUI-scrape `card.tokens` field, which is now null for
// every session: a stale scrape on one device, a flat lying 0 on every
// other). Returns null (render a placeholder, never 0) only when this
// device has live sessions but none of them have reported a `usage` field
// yet, i.e. we cannot vouch for any number. Zero live sessions is a
// legitimate, vouched-for 0, not an unknown: there is nothing running to
// have accrued usage. A session with usage === null (backend confirmed no
// transcript data) contributes 0, same distinction fmtUsage above makes.
// Callers must additionally gate this on their own "has /api/fleet loaded
// at all yet" check (see useFleet's connected/usingFallback): an empty
// sessions array before the first fleet frame arrives must not be read as
// "confirmed zero devices with sessions".
export function deviceEffectiveTokens(
  deviceId: string,
  sessions: { device_id: string; usage?: { effective: number } | null }[],
): number | null {
  const deviceSessions = sessions.filter((s) => s.device_id === deviceId);
  const reporting = deviceSessions.filter((s) => s.usage !== undefined);
  if (deviceSessions.length > 0 && reporting.length === 0) return null;
  let sum = 0;
  for (const s of reporting) {
    if (s.usage) sum += s.usage.effective;
  }
  return sum;
}

// Adds an alpha channel to an existing RT/FN oklch(...) token string, for a
// tinted background or border derived from a palette color. Keeps the
// derived color tied to its source token instead of duplicating the
// token's L/C/H values as a second literal that can drift out of sync, and
// avoids appending a hex alpha suffix directly to an oklch() string (that
// syntax only works on #rrggbb hex colors, not on functional notations, so
// it silently produces invalid CSS that the browser drops).
export const withAlpha = (oklchColor: string, alpha: number): string =>
  oklchColor.replace(/\)$/, ` / ${alpha})`);

// Tokens-bar color: green → amber → red as capacity fills.
export const capColor = (pct: number): string => (pct >= 90 ? FN.red : pct >= 75 ? FN.amber : FN.green);

// Stable hash: device id → hue (0–359).
export const hueForId = (id: string): number => {
  let h = 0;
  for (const c of id) h = (h * 31 + c.charCodeAt(0)) % 360;
  return h;
};

// OS string → icon key ('laptop' | 'server').
export const kindForOs = (os: string): 'laptop' | 'server' =>
  /mac/i.test(os) ? 'laptop' : 'server';

// Format an ISO date string to a short human-readable label.
export const fmtDate = (iso: string): string => {
  try {
    return new Date(iso).toLocaleDateString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
  } catch {
    return iso;
  }
};
