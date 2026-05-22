// tokens.jsx — shared design tokens, mock data, helpers, primitives.
// Exported to window so the three variant scripts can read them.

const FONT_SANS  = "'Geist', system-ui, sans-serif";
const FONT_SERIF = "'Instrument Serif', Georgia, serif";
const FONT_MONO  = "'Geist Mono', ui-monospace, SFMono-Regular, monospace";

// Functional palette — shared across variants. OKLCH for predictable lightness.
const FN = {
  blue:  'oklch(0.66 0.14 250)',
  green: 'oklch(0.70 0.15 150)',
  amber: 'oklch(0.78 0.14 78)',
  red:   'oklch(0.66 0.18 25)',
};

// Dark theme tokens (Mission + Ops variants).
const DARK = {
  bg:        'oklch(0.165 0.006 80)',
  bgRaised:  'oklch(0.205 0.008 80)',
  panel:     'oklch(0.225 0.008 80)',
  card:      'oklch(0.245 0.008 80)',
  border:    'oklch(0.30 0.008 80)',
  borderHi:  'oklch(0.38 0.010 80)',
  text:      'oklch(0.96 0.005 80)',
  textDim:   'oklch(0.70 0.008 80)',
  textLow:   'oklch(0.50 0.008 80)',
  ...FN,
};

// Light theme tokens (Atelier variant).
const LIGHT = {
  bg:        'oklch(0.975 0.010 80)',
  bgRaised:  'oklch(0.992 0.006 80)',
  panel:     '#ffffff',
  card:      '#ffffff',
  border:    'oklch(0.90 0.012 80)',
  borderHi:  'oklch(0.82 0.014 80)',
  text:      'oklch(0.22 0.012 60)',
  textDim:   'oklch(0.50 0.012 60)',
  textLow:   'oklch(0.66 0.010 60)',
  ...FN,
};

// Devices — each owns a hue used for color-coding across variants.
const DEVICES = [
  {
    id: 'mbp',  name: 'macbook-claude', hostname: 'mbp-claude.local',
    online: true,  os: 'macOS 15.2',  location: 'New York · US',  region: 'NYC',
    cpuLoad: 0.34, tokens: 154000, tokenCap: 200000, sessions: 3,
    lastActivity: 'just now', lastSeenSec: 5,
    hue: 250, kind: 'laptop',
    spark: [12, 18, 35, 60, 45, 70, 110, 92, 88, 105, 130, 154],
  },
  {
    id: 'prod', name: 'prod-server-1', hostname: 'prod-1.fra.internal',
    online: true,  os: 'Ubuntu 24.04', location: 'Frankfurt · DE', region: 'FRA',
    cpuLoad: 0.78, tokens: 458000, tokenCap: 500000, sessions: 5,
    lastActivity: '2m ago', lastSeenSec: 120,
    hue: 30, kind: 'server',
    spark: [220, 240, 280, 310, 340, 380, 400, 420, 440, 450, 455, 458],
  },
  {
    id: 'rpi',  name: 'rpi-home', hostname: 'pi.local',
    online: false, os: 'Raspberry Pi OS', location: 'Brooklyn · US', region: 'NYC',
    cpuLoad: 0,   tokens: 0,      tokenCap: 200000, sessions: 0,
    lastActivity: '3h ago', lastSeenSec: 10800,
    hue: 150, kind: 'edge',
    spark: [40, 32, 28, 12, 0, 0, 0, 0, 0, 0, 0, 0],
  },
  {
    id: 'dev',  name: 'dev-vm', hostname: 'ec2-dev.us-east-1',
    online: true,  os: 'Ubuntu 22.04', location: 'us-east-1', region: 'IAD',
    cpuLoad: 0.22, tokens: 66000,  tokenCap: 200000, sessions: 2,
    lastActivity: '15m ago', lastSeenSec: 900,
    hue: 290, kind: 'vm',
    spark: [20, 25, 30, 40, 45, 55, 60, 62, 64, 65, 66, 66],
  },
  {
    id: 'gpu',  name: 'gpu-rig', hostname: 'gpu-rig.tail-net',
    online: true,  os: 'Pop!_OS 22.04', location: 'San Jose · US', region: 'SJC',
    cpuLoad: 0.91, tokens: 319000, tokenCap: 400000, sessions: 4,
    lastActivity: '1m ago', lastSeenSec: 60,
    hue: 75, kind: 'workstation',
    spark: [100, 120, 145, 180, 210, 240, 260, 280, 290, 305, 315, 319],
  },
];

// Hue → CSS color helpers (consistent chroma/lightness so device hues feel like a family).
const devColor   = (hue, L = 0.70, C = 0.14) => `oklch(${L} ${C} ${hue})`;
const devSoft    = (hue) => `oklch(0.70 0.14 ${hue} / 0.16)`;
const devSofter  = (hue) => `oklch(0.70 0.14 ${hue} / 0.08)`;
const devGlow    = (hue) => `oklch(0.78 0.16 ${hue})`;

// Sessions keyed by device id.
const SESSIONS = {
  mbp: [
    { name: 'rc-travel2',  dir: 'root',        tokensK: 154, pct: 77, mode: 'STANDARD', perms: 'skip-perms', sessionId: 'session_01PdGuCB4Bsbv7JmcSWtKEhA', status: 'thinking' },
    { name: 'rc-viewlogic',dir: 'root',        tokensK: 458, pct: 91, mode: 'STANDARD', perms: 'skip-perms', sessionId: 'session_013RMteDkWHb2ALEZB6so4V1', status: 'idle' },
    { name: 'rc-launcher', dir: 'rc-launcher', tokensK: 0,   pct:  0, mode: 'STANDARD', perms: 'skip-perms', sessionId: 'session_01KaYegnnfrYXnUTepogPP8F', status: 'idle' },
  ],
  prod: [
    { name: 'rc-neonspace',         dir: 'neonspace', tokensK: 319, pct: 80, mode: 'STANDARD', perms: 'skip-perms', sessionId: 'session_01FBFPHGAQNBGdWCC1SLiFHM', status: 'running' },
    { name: 'rc-sched-apply',       dir: 'root',     tokensK:  66, pct: 33, mode: 'STANDARD', perms: 'skip-perms', sessionId: 'session_014pbkVqFFyCs2qpPzsAP83z', status: 'running' },
    { name: 'rc-prod-deploy',       dir: 'deploy',   tokensK:  22, pct: 11, mode: 'TEAMMATE', perms: 'skip-perms', sessionId: 'session_01prodDeployXYZ12345',     status: 'running' },
    { name: 'rc-monitor',           dir: 'monitor',  tokensK:  51, pct: 25, mode: 'STANDARD', perms: 'skip-perms', sessionId: 'session_01monitorAB678910',        status: 'idle' },
    { name: 'chaincentral-email',   dir: 'cce',      tokensK: 102, pct: 51, mode: 'STANDARD', perms: 'skip-perms', sessionId: 'session_01cceXX222333',           status: 'thinking' },
  ],
  rpi: [],
  dev: [
    { name: 'rc-dev-cleanup', dir: 'app', tokensK: 33, pct: 16, mode: 'SAFE',     perms: 'standard',   sessionId: 'session_01devCleanupAB',  status: 'idle' },
    { name: 'rc-dev-tests',   dir: 'app', tokensK: 33, pct: 16, mode: 'STANDARD', perms: 'skip-perms', sessionId: 'session_01devTestsCD',    status: 'running' },
  ],
  gpu: [
    { name: 'rc-train-1',  dir: 'ml/runs', tokensK: 89, pct: 89, mode: 'STANDARD', perms: 'skip-perms', sessionId: 'session_01trainAA',  status: 'running' },
    { name: 'rc-train-2',  dir: 'ml/runs', tokensK: 91, pct: 91, mode: 'STANDARD', perms: 'skip-perms', sessionId: 'session_01trainBB',  status: 'running' },
    { name: 'rc-finetune', dir: 'ml/ft',   tokensK: 78, pct: 78, mode: 'STANDARD', perms: 'skip-perms', sessionId: 'session_01ftXX9999', status: 'thinking' },
    { name: 'rc-eval',     dir: 'ml/eval', tokensK: 61, pct: 61, mode: 'TEAMMATE', perms: 'skip-perms', sessionId: 'session_01evalYY01', status: 'idle' },
  ],
};

// Scheduled tasks (shared, but tagged with device id).
const SCHEDULED = [
  { id: 'cce', name: 'chaincentral-cold-email', device: 'prod', cron: '0 5 * * *', schedule: 'At 9:00', mode: 'STANDARD', enabled: true,  dir: '/root', lastRunDaysAgo: 42 },
  { id: 'red', name: 'reddit-agent',            device: 'prod', cron: '30 9,19 * * *', schedule: 'At 13:30', mode: 'STANDARD', enabled: false, dir: '/root', lastRunDaysAgo: 36 },
  { id: 'pipe',name: 'pipeout-launch',          device: 'mbp',  cron: '0 10 * * *', schedule: 'At 10:00', mode: 'STANDARD', enabled: true,  dir: '/var/www', lastRunDaysAgo: 23 },
  { id: 'jobs',name: 'apply-tech-jobs',         device: 'gpu',  cron: '0 22 * * *', schedule: 'At 22:00', mode: 'STANDARD', enabled: true,  dir: '/root',    lastRunDaysAgo: 1 },
];

// Aggregates.
const totalTokens     = DEVICES.reduce((s, d) => s + d.tokens, 0);
const totalTokenCap   = DEVICES.reduce((s, d) => s + d.tokenCap, 0);
const totalSessions   = DEVICES.reduce((s, d) => s + d.sessions, 0);
const onlineCount     = DEVICES.filter((d) => d.online).length;
const offlineCount    = DEVICES.length - onlineCount;

// Format helpers.
const fmtK = (n) => {
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(n >= 10_000_000 ? 0 : 1) + 'M';
  if (n >= 1000)      return (n / 1000).toFixed(0) + 'K';
  return String(n);
};
const fmtPct = (n) => Math.round(n) + '%';

// Tokens-bar color: green → amber → red as capacity fills.
const capColor = (pct) => pct >= 90 ? FN.red : pct >= 75 ? FN.amber : FN.green;

// ─── Primitives ─────────────────────────────────────────────────────────────

function Dot({ color = FN.green, size = 8, pulse = false }) {
  const pulseStyle = pulse ? {
    boxShadow: `0 0 0 0 ${color}`,
    animation: 'rc-pulse 1.8s ease-out infinite',
  } : { boxShadow: `0 0 6px ${color}` };
  return (
    <span style={{ display: 'inline-block', width: size, height: size, borderRadius: size, background: color, flex: 'none', ...pulseStyle }} />
  );
}

// Inject pulse keyframes once.
if (typeof document !== 'undefined' && !document.getElementById('rc-keyframes')) {
  const s = document.createElement('style');
  s.id = 'rc-keyframes';
  s.textContent = `
    @keyframes rc-pulse {
      0%   { box-shadow: 0 0 0 0 currentColor; }
      70%  { box-shadow: 0 0 0 6px transparent; }
      100% { box-shadow: 0 0 0 0 transparent; }
    }
    @keyframes rc-spin { to { transform: rotate(360deg); } }
    @keyframes rc-shimmer {
      0%, 100% { opacity: 0.55; }
      50%      { opacity: 1; }
    }
  `;
  document.head.appendChild(s);
}

// Sparkline — tiny SVG line+area for 12 data points.
function Sparkline({ data, w = 80, h = 22, color = 'currentColor', fillOpacity = 0.18, dotEnd = false }) {
  if (!data || data.length === 0) return <svg width={w} height={h} />;
  const max = Math.max(...data, 1);
  const stepX = data.length > 1 ? w / (data.length - 1) : w;
  const pts = data.map((v, i) => [i * stepX, h - (v / max) * (h - 2) - 1]);
  const d = pts.map((p, i) => (i === 0 ? 'M' : 'L') + p[0].toFixed(1) + ' ' + p[1].toFixed(1)).join(' ');
  const area = d + ` L${w.toFixed(1)} ${h} L0 ${h} Z`;
  const last = pts[pts.length - 1];
  return (
    <svg width={w} height={h} viewBox={`0 0 ${w} ${h}`} style={{ display: 'block', overflow: 'visible' }}>
      <path d={area} fill={color} opacity={fillOpacity} />
      <path d={d} fill="none" stroke={color} strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" />
      {dotEnd && <circle cx={last[0]} cy={last[1]} r="2" fill={color} />}
    </svg>
  );
}

// Token capacity bar.
function CapBar({ pct, height = 4, bg, color }) {
  const c = color || capColor(pct);
  return (
    <div style={{ height, borderRadius: height, background: bg || 'rgba(255,255,255,.06)', overflow: 'hidden', flex: 1, minWidth: 40 }}>
      <div style={{ height: '100%', width: Math.min(100, pct) + '%', background: c, borderRadius: height, transition: 'width .3s ease' }} />
    </div>
  );
}

// Icon helpers — minimal stroked SVG glyphs.
const I = ({ d, size = 14, sw = 1.6, fill = 'none', stroke = 'currentColor', children, vb = 24 }) => (
  <svg width={size} height={size} viewBox={`0 0 ${vb} ${vb}`} fill={fill} stroke={stroke} strokeWidth={sw} strokeLinecap="round" strokeLinejoin="round" style={{ flex: 'none' }}>
    {children || <path d={d} />}
  </svg>
);

const Icons = {
  laptop: (p)  => <I {...p}><rect x="3" y="5" width="18" height="12" rx="1.5" /><path d="M2 19h20" /></I>,
  server: (p)  => <I {...p}><rect x="3" y="4"  width="18" height="7" rx="1.5" /><rect x="3" y="13" width="18" height="7" rx="1.5" /><circle cx="7" cy="7.5" r=".6" fill="currentColor" /><circle cx="7" cy="16.5" r=".6" fill="currentColor" /></I>,
  edge: (p)    => <I {...p}><rect x="4" y="4" width="16" height="16" rx="2" /><rect x="9" y="9" width="6" height="6" rx="1" /></I>,
  vm: (p)      => <I {...p}><rect x="3" y="3" width="11" height="11" rx="1.5" /><rect x="10" y="10" width="11" height="11" rx="1.5" /></I>,
  workstation: (p) => <I {...p}><rect x="3" y="3" width="18" height="14" rx="1.5" /><path d="M9 21h6M12 17v4" /></I>,

  play:    (p) => <I {...p}><path d="M7 5l11 7-11 7V5z" fill="currentColor" stroke="none" /></I>,
  pause:   (p) => <I {...p}><rect x="6" y="5" width="4" height="14" rx="1" fill="currentColor" stroke="none" /><rect x="14" y="5" width="4" height="14" rx="1" fill="currentColor" stroke="none" /></I>,
  stop:    (p) => <I {...p}><rect x="6" y="6" width="12" height="12" rx="1.5" fill="currentColor" stroke="none" /></I>,
  refresh: (p) => <I {...p}><path d="M3 12a9 9 0 0 1 15.5-6.3M21 4v5h-5" /><path d="M21 12a9 9 0 0 1-15.5 6.3M3 20v-5h5" /></I>,
  copy:    (p) => <I {...p}><rect x="8" y="8" width="12" height="12" rx="2" /><path d="M4 16V6a2 2 0 0 1 2-2h10" /></I>,
  plus:    (p) => <I {...p}><path d="M12 5v14M5 12h14" /></I>,
  back:    (p) => <I {...p}><path d="M15 18l-6-6 6-6" /></I>,
  forward: (p) => <I {...p}><path d="M9 6l6 6-6 6" /></I>,
  share:   (p) => <I {...p}><circle cx="6" cy="12" r="2.5" /><circle cx="18" cy="6" r="2.5" /><circle cx="18" cy="18" r="2.5" /><path d="M8.2 10.8l7.6-3.6M8.2 13.2l7.6 3.6" /></I>,
  power:   (p) => <I {...p}><path d="M12 3v9" /><path d="M5.6 7.5a8 8 0 1 0 12.8 0" /></I>,
  globe:   (p) => <I {...p}><circle cx="12" cy="12" r="9" /><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18" /></I>,
  clock:   (p) => <I {...p}><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></I>,
  folder:  (p) => <I {...p}><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7z" /></I>,
  terminal:(p) => <I {...p}><rect x="3" y="4" width="18" height="16" rx="2" /><path d="M7 9l3 3-3 3M13 15h4" /></I>,
  search:  (p) => <I {...p}><circle cx="11" cy="11" r="7" /><path d="M20 20l-3.5-3.5" /></I>,
  chevDown:(p) => <I {...p}><path d="M6 9l6 6 6-6" /></I>,
  chevRight:(p)=> <I {...p}><path d="M9 6l6 6-6 6" /></I>,
  more:    (p) => <I {...p}><circle cx="6" cy="12" r="1.4" fill="currentColor" stroke="none" /><circle cx="12" cy="12" r="1.4" fill="currentColor" stroke="none" /><circle cx="18" cy="12" r="1.4" fill="currentColor" stroke="none" /></I>,
  link:    (p) => <I {...p}><path d="M10 14a4 4 0 0 0 5.6 0l3-3a4 4 0 1 0-5.6-5.6L11 7" /><path d="M14 10a4 4 0 0 0-5.6 0l-3 3a4 4 0 1 0 5.6 5.6L13 17" /></I>,
  spinner: (p) => <I {...p}><path d="M21 12a9 9 0 1 1-9-9" opacity=".25" /><path d="M21 12a9 9 0 0 0-9-9" /></I>,
  filter:  (p) => <I {...p}><path d="M4 5h16l-6 8v6l-4-2v-4L4 5z" /></I>,
};

// Status pill ─────────────────────────────────────────────────────────────
function StatusPill({ status, tokens }) {
  const map = {
    running:  { label: 'running',  color: FN.green,  pulse: true },
    thinking: { label: 'thinking', color: FN.amber,  pulse: true },
    idle:     { label: 'idle',     color: 'oklch(0.62 0.008 80)', pulse: false },
    stopped:  { label: 'stopped',  color: FN.red,    pulse: false },
  };
  const m = map[status] || map.idle;
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 11, color: m.color, letterSpacing: '.04em', textTransform: 'uppercase', fontFamily: FONT_MONO }}>
      <span style={{ color: m.color }}><Dot color={m.color} size={6} pulse={m.pulse} /></span>
      {m.label}
    </span>
  );
}

Object.assign(window, {
  FONT_SANS, FONT_SERIF, FONT_MONO, FN, DARK, LIGHT,
  DEVICES, SESSIONS, SCHEDULED,
  totalTokens, totalTokenCap, totalSessions, onlineCount, offlineCount,
  fmtK, fmtPct, capColor, devColor, devSoft, devSofter, devGlow,
  Dot, Sparkline, CapBar, Icons, StatusPill,
});
