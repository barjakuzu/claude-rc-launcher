// primitives.tsx — shared UI primitives: Dot, Sparkline, CapBar, Icons, StatusPill.
import type { ReactNode } from 'react';
import { FONT_MONO, FN, RT, capColor } from '../tokens';

// Inject keyframes once.
export function ensureKeyframes(): void {
  if (typeof document === 'undefined' || document.getElementById('rc-keyframes')) return;
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

export interface DotProps {
  color?: string;
  size?: number;
  pulse?: boolean;
}

export function Dot({ color = FN.green, size = 8, pulse = false }: DotProps) {
  const pulseStyle = pulse
    ? { boxShadow: `0 0 0 0 ${color}`, animation: 'rc-pulse 1.8s ease-out infinite' }
    : { boxShadow: `0 0 6px ${color}` };
  return (
    <span
      style={{
        display: 'inline-block',
        width: size,
        height: size,
        borderRadius: size,
        background: color,
        flex: 'none',
        ...pulseStyle,
      }}
    />
  );
}

export interface SparklineProps {
  data: number[];
  w?: number;
  h?: number;
  color?: string;
  fillOpacity?: number;
  dotEnd?: boolean;
  /** When true, the SVG width is 100% of its container; w only drives the viewBox math. */
  responsive?: boolean;
}

export function Sparkline({ data, w = 80, h = 22, color = 'currentColor', fillOpacity = 0.18, dotEnd = false, responsive = false }: SparklineProps) {
  const svgW: number | string = responsive ? '100%' : w;
  if (!data || data.length === 0) return <svg width={svgW} height={h} viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" />;
  const max = Math.max(...data, 1);
  const stepX = data.length > 1 ? w / (data.length - 1) : w;
  const pts: [number, number][] = data.map((v, i) => [i * stepX, h - (v / max) * (h - 2) - 1]);
  const d = pts.map((p, i) => (i === 0 ? 'M' : 'L') + p[0].toFixed(1) + ' ' + p[1].toFixed(1)).join(' ');
  const area = d + ` L${w.toFixed(1)} ${h} L0 ${h} Z`;
  const last = pts[pts.length - 1];
  return (
    <svg width={svgW} height={h} viewBox={`0 0 ${w} ${h}`} preserveAspectRatio={responsive ? 'none' : undefined} style={{ display: 'block', overflow: 'visible' }}>
      <path d={area} fill={color} opacity={fillOpacity} />
      <path d={d} fill="none" stroke={color} strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" />
      {dotEnd && <circle cx={last[0]} cy={last[1]} r="2" fill={color} />}
    </svg>
  );
}

export interface CapBarProps {
  pct: number;
  height?: number;
  bg?: string;
  color?: string;
}

export function CapBar({ pct, height = 4, bg, color }: CapBarProps) {
  const c = color || capColor(pct);
  return (
    <div style={{ height, borderRadius: height, background: bg || 'rgba(255,255,255,.06)', overflow: 'hidden', flex: 1, minWidth: 40 }}>
      <div style={{ height: '100%', width: Math.min(100, pct) + '%', background: c, borderRadius: height, transition: 'width .3s ease' }} />
    </div>
  );
}

// Icon helpers — minimal stroked SVG glyphs.
interface IProps {
  d?: string;
  size?: number;
  sw?: number;
  fill?: string;
  stroke?: string;
  children?: ReactNode;
  vb?: number;
}

const I = ({ d, size = 14, sw = 1.6, fill = 'none', stroke = 'currentColor', children, vb = 24 }: IProps) => (
  <svg
    width={size}
    height={size}
    viewBox={`0 0 ${vb} ${vb}`}
    fill={fill}
    stroke={stroke}
    strokeWidth={sw}
    strokeLinecap="round"
    strokeLinejoin="round"
    style={{ flex: 'none' }}
  >
    {children || <path d={d} />}
  </svg>
);

export type IconProps = Omit<IProps, 'children' | 'd'>;
type IconFn = (p: IconProps) => ReactNode;

export const Icons: Record<string, IconFn> = {
  laptop: (p) => <I {...p}><rect x="3" y="5" width="18" height="12" rx="1.5" /><path d="M2 19h20" /></I>,
  server: (p) => <I {...p}><rect x="3" y="4" width="18" height="7" rx="1.5" /><rect x="3" y="13" width="18" height="7" rx="1.5" /><circle cx="7" cy="7.5" r=".6" fill="currentColor" /><circle cx="7" cy="16.5" r=".6" fill="currentColor" /></I>,
  edge: (p) => <I {...p}><rect x="4" y="4" width="16" height="16" rx="2" /><rect x="9" y="9" width="6" height="6" rx="1" /></I>,
  vm: (p) => <I {...p}><rect x="3" y="3" width="11" height="11" rx="1.5" /><rect x="10" y="10" width="11" height="11" rx="1.5" /></I>,
  workstation: (p) => <I {...p}><rect x="3" y="3" width="18" height="14" rx="1.5" /><path d="M9 21h6M12 17v4" /></I>,

  play: (p) => <I {...p}><path d="M7 5l11 7-11 7V5z" fill="currentColor" stroke="none" /></I>,
  pause: (p) => <I {...p}><rect x="6" y="5" width="4" height="14" rx="1" fill="currentColor" stroke="none" /><rect x="14" y="5" width="4" height="14" rx="1" fill="currentColor" stroke="none" /></I>,
  stop: (p) => <I {...p}><rect x="6" y="6" width="12" height="12" rx="1.5" fill="currentColor" stroke="none" /></I>,
  refresh: (p) => <I {...p}><path d="M3 12a9 9 0 0 1 15.5-6.3M21 4v5h-5" /><path d="M21 12a9 9 0 0 1-15.5 6.3M3 20v-5h5" /></I>,
  copy: (p) => <I {...p}><rect x="8" y="8" width="12" height="12" rx="2" /><path d="M4 16V6a2 2 0 0 1 2-2h10" /></I>,
  plus: (p) => <I {...p}><path d="M12 5v14M5 12h14" /></I>,
  back: (p) => <I {...p}><path d="M15 18l-6-6 6-6" /></I>,
  forward: (p) => <I {...p}><path d="M9 6l6 6-6 6" /></I>,
  share: (p) => <I {...p}><circle cx="6" cy="12" r="2.5" /><circle cx="18" cy="6" r="2.5" /><circle cx="18" cy="18" r="2.5" /><path d="M8.2 10.8l7.6-3.6M8.2 13.2l7.6 3.6" /></I>,
  power: (p) => <I {...p}><path d="M12 3v9" /><path d="M5.6 7.5a8 8 0 1 0 12.8 0" /></I>,
  globe: (p) => <I {...p}><circle cx="12" cy="12" r="9" /><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18" /></I>,
  clock: (p) => <I {...p}><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></I>,
  folder: (p) => <I {...p}><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7z" /></I>,
  terminal: (p) => <I {...p}><rect x="3" y="4" width="18" height="16" rx="2" /><path d="M7 9l3 3-3 3M13 15h4" /></I>,
  search: (p) => <I {...p}><circle cx="11" cy="11" r="7" /><path d="M20 20l-3.5-3.5" /></I>,
  chevDown: (p) => <I {...p}><path d="M6 9l6 6 6-6" /></I>,
  chevRight: (p) => <I {...p}><path d="M9 6l6 6-6 6" /></I>,
  more: (p) => <I {...p}><circle cx="6" cy="12" r="1.4" fill="currentColor" stroke="none" /><circle cx="12" cy="12" r="1.4" fill="currentColor" stroke="none" /><circle cx="18" cy="12" r="1.4" fill="currentColor" stroke="none" /></I>,
  link: (p) => <I {...p}><path d="M10 14a4 4 0 0 0 5.6 0l3-3a4 4 0 1 0-5.6-5.6L11 7" /><path d="M14 10a4 4 0 0 0-5.6 0l-3 3a4 4 0 1 0 5.6 5.6L13 17" /></I>,
  spinner: (p) => <I {...p}><path d="M21 12a9 9 0 1 1-9-9" opacity=".25" /><path d="M21 12a9 9 0 0 0-9-9" /></I>,
  filter: (p) => <I {...p}><path d="M4 5h16l-6 8v6l-4-2v-4L4 5z" /></I>,
  edit: (p) => <I {...p}><path d="M12 20h9" /><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z" /></I>,
};

// Status pill — uses the RT palette mapping from the V4 panel.
export interface StatusPillProps {
  status: string;
}

interface PillMeta {
  label: string;
  color: string;
  pulse: boolean;
}

export function StatusPill({ status }: StatusPillProps) {
  const map: Record<string, PillMeta> = {
    running: { label: 'running', color: RT.green, pulse: true },
    thinking: { label: 'thinking', color: RT.amber, pulse: true },
    idle: { label: 'idle', color: RT.textLow, pulse: false },
    stopped: { label: 'stopped', color: RT.red, pulse: false },
  };
  const m: PillMeta = map[status] || { label: status, color: RT.textLow, pulse: false };
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontSize: 10, color: m.color, letterSpacing: '.06em', textTransform: 'uppercase', fontFamily: FONT_MONO }}>
      <Dot color={m.color} size={5} pulse={m.pulse} />
      {m.label}
    </span>
  );
}
