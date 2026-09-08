// Strip.tsx — RStrip (aggregate strip). Desktop only.
import type { DeviceCard } from '../types';
import { RT, FONT_MONO, fmtK } from '../tokens';
import { Dot, CapBar } from './primitives';

interface StripProps {
  cards: DeviceCard[];
  /** Live effective-token total across devices we can vouch for (App.tsx),
   * derived from /api/fleet's per-session usage — not the old TUI-scrape
   * `card.tokens` field, which is null for every session now. null means
   * the fleet hasn't reported enough to vouch for any total yet: render a
   * placeholder, never a 0 that looks like a confirmed empty fleet. */
  totalTokens: number | null;
}

export function Strip({ cards, totalTokens }: StripProps) {
  const onlineCount = cards.filter((c) => c.online).length;
  const offlineCount = cards.length - onlineCount;
  const totalSessions = cards.reduce((s, c) => s + c.sessions, 0);

  const onlineCards = cards.filter((c) => c.online);
  const avgLoad = onlineCards.length > 0
    ? Math.round(onlineCards.reduce((s, c) => s + c.loadPct, 0) / onlineCards.length)
    : 0;

  type Cell = { label: string; value: string; sub?: string; dot?: string; bar?: number };
  const cells: Cell[] = [
    { label: 'Online',    value: `${onlineCount}/${cards.length}`, sub: `${offlineCount} offline`, dot: RT.green },
    { label: 'Sessions',  value: String(totalSessions),             sub: 'running' },
    { label: 'Effective', value: totalTokens != null ? fmtK(totalTokens) : '—', sub: 'tokens' },
    { label: 'Load',      value: `${avgLoad}%`,                    bar: avgLoad },
  ];

  return (
    <div style={{
      flex: 'none',
      borderBottom: `1px solid ${RT.border}`,
      background: RT.bg,
      display: 'grid',
      gridTemplateColumns: `repeat(${cells.length}, 1fr)`,
      padding: '12px 0',
    }}>
      {cells.map((c, i) => (
        <div key={c.label} style={{ padding: '0 22px', borderLeft: i === 0 ? 'none' : `1px solid ${RT.border}` }}>
          <div style={{ fontSize: 10, color: RT.textLow, letterSpacing: '.14em', textTransform: 'uppercase', fontFamily: FONT_MONO, marginBottom: 6 }}>{c.label}</div>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 7 }}>
            {c.dot && <Dot color={c.dot} size={6} pulse />}
            <div style={{ fontSize: 22, fontWeight: 500, letterSpacing: '-.02em', fontFamily: FONT_MONO, lineHeight: 1 }}>{c.value}</div>
            {c.sub && <div style={{ fontSize: 11, color: RT.textLow, fontFamily: FONT_MONO }}>{c.sub}</div>}
          </div>
          {c.bar != null && (
            <div style={{ marginTop: 7 }}>
              <CapBar pct={c.bar} height={3} bg="rgba(255,255,255,.04)" color={RT.accent} />
            </div>
          )}
        </div>
      ))}
    </div>
  );
}
