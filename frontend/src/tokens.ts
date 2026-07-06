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
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(n >= 10_000_000 ? 0 : 1) + 'M';
  if (n >= 1000) return (n / 1000).toFixed(0) + 'K';
  return String(n);
};
export const fmtPct = (n: number): string => Math.round(n) + '%';

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
