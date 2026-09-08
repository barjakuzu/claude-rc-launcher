// Strip.tsx — RStrip (aggregate strip). Desktop only.
//
// Round 3: dropped the Load cell and gave that width to LimitsSummary.
// Load (average CPU) is the one number here that's already visible per
// device on every card below; it was the natural thing to cut to make
// room for account limits, which the user explicitly named as more
// important than everything else in this row ("the thing that actually
// constrains their day"). The row is now flex, not an equal-width grid:
// Online/Sessions/Effective keep their content-driven width, and
// LimitsSummary takes the remaining space, since it has two numbers (plus
// their countdowns) to show rather than one.
import type { DeviceCard } from '../types';
import { RT, FONT_MONO, fmtK } from '../tokens';
import { Dot } from './primitives';
import { LimitsSummary } from './LimitsSummary';

interface StripProps {
  cards: DeviceCard[];
  /** Live effective-token total across devices we can vouch for (App.tsx),
   * derived from /api/fleet's per-session usage, not the old TUI-scrape
   * `card.tokens` field, which is null for every session now. null means
   * the fleet hasn't reported enough to vouch for any total yet: render a
   * placeholder, never a 0 that looks like a confirmed empty fleet. */
  totalTokens: number | null;
  /** False until /rc/overview has answered at least once (App.tsx).
   * Round 4: Online and Sessions were reading `cards` directly with no
   * such gate, so the same fabricated-zero window the Effective fix
   * already covers was showing a confident "Online 0/0" / "Sessions 0"
   * during the brief pre-load period after mount, before cards had ever
   * been populated. cards.length is 0 in both "confirmed empty fleet" and
   * "haven't heard yet"; only hasLoadedCards tells them apart. */
  hasLoadedCards: boolean;
}

export function Strip({ cards, totalTokens, hasLoadedCards }: StripProps) {
  const onlineCount = cards.filter((c) => c.online).length;
  const offlineCount = cards.length - onlineCount;
  const totalSessions = cards.reduce((s, c) => s + c.sessions, 0);

  type Cell = { label: string; value: string; sub?: string; dot?: string };
  const cells: Cell[] = [
    {
      label: 'Online',
      value: hasLoadedCards ? `${onlineCount}/${cards.length}` : '—/—',
      sub: hasLoadedCards ? `${offlineCount} offline` : undefined,
      dot: hasLoadedCards ? RT.green : undefined,
    },
    {
      label: 'Sessions',
      value: hasLoadedCards ? String(totalSessions) : '—',
      sub: hasLoadedCards ? 'running' : undefined,
    },
    { label: 'Effective', value: totalTokens != null ? fmtK(totalTokens) : '—', sub: 'tokens' },
  ];

  return (
    <div style={{
      flex: 'none',
      borderBottom: `1px solid ${RT.border}`,
      background: RT.bg,
      display: 'flex',
      alignItems: 'stretch',
      padding: '12px 0',
    }}>
      {cells.map((c, i) => (
        <div key={c.label} style={{ padding: '0 22px', flex: 'none', borderLeft: i === 0 ? 'none' : `1px solid ${RT.border}` }}>
          <div style={{ fontSize: 10, color: RT.textLow, letterSpacing: '.14em', textTransform: 'uppercase', fontFamily: FONT_MONO, marginBottom: 6 }}>{c.label}</div>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 7 }}>
            {c.dot && <Dot color={c.dot} size={6} pulse />}
            <div style={{ fontSize: 22, fontWeight: 500, letterSpacing: '-.02em', fontFamily: FONT_MONO, lineHeight: 1 }}>{c.value}</div>
            {c.sub && <div style={{ fontSize: 11, color: RT.textLow, fontFamily: FONT_MONO }}>{c.sub}</div>}
          </div>
        </div>
      ))}
      <div style={{ padding: '0 22px', borderLeft: `1px solid ${RT.border}`, flex: 1, minWidth: 0 }}>
        <LimitsSummary mobile={false} />
      </div>
    </div>
  );
}
