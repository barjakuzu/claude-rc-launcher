// BigCard.tsx: V5 device card for the overview grid.
//
// Round 3: dropped the sparkline and the redundant "Open" button/row.
// Two things drove this, not one: the sparkline's source (card.spark, the
// same old TUI-scrape vintage as the card.tokens field Round 2 replaced)
// produced a broken-looking solid box for at least one real device
// (degenerate data, desktop rendered it unconditionally, with no hasSpark
// guard the mobile branch already had), and a decorative trend line
// standing in for content is a flagged default regardless. The "Open"
// button was always redundant with the card's own onClick (the whole card
// has been a click target since V5Stat existed), so a trailing chevron is
// enough of an affordance, matching the icon-only pattern used elsewhere
// in this app (Header.tsx's MachineSelector rows, for one). Together this
// roughly halves the card's height: two content rows (header, stats)
// instead of four, so meaningfully more devices are visible per screen
// without scrolling. That is the actual complaint, not a decoration problem.
import { useState } from 'react';
import { RT, FONT_MONO, tintFor, tintSoft, tintEdge, hueForId, fmtK, kindForOs } from '../tokens';
import { Dot, CapBar, Icons } from './primitives';
import type { DeviceCard, DeviceUsage } from '../types';

interface BigCardProps {
  card: DeviceCard;
  cards: DeviceCard[];
  onClick: () => void;
  mobile?: boolean;
  /** Live effective-token reading for this device (App.tsx, from
   * /api/fleet's per-session usage), replaces the old TUI-scrape
   * card.tokens field, which is null for every session now. */
  usage: DeviceUsage;
}

function V5Stat({ label, value, bar, barColor, sub, subColor }: {
  label: string; value: string | number;
  bar?: number; barColor?: string; sub?: string; subColor?: string;
}) {
  return (
    <div>
      <div style={{
        fontSize: 9, color: RT.textLow, letterSpacing: '.14em',
        textTransform: 'uppercase', fontFamily: FONT_MONO, marginBottom: 5,
      }}>
        {label}
      </div>
      <div style={{ fontSize: 16, fontWeight: 500, letterSpacing: '-.015em', fontFamily: FONT_MONO }}>
        {value}
      </div>
      {bar != null && (
        <div style={{ marginTop: 6 }}>
          <CapBar pct={bar} height={3} bg="rgba(255,255,255,.05)" color={barColor || RT.accent} />
        </div>
      )}
      {sub && (
        <div style={{ fontSize: 10, color: subColor || RT.textLow, marginTop: 4, fontFamily: FONT_MONO }}>{sub}</div>
      )}
    </div>
  );
}

export function BigCard({ card, cards, onClick, mobile = false, usage }: BigCardProps) {
  const hue = hueForId(card.id);
  const KindIcon = Icons[kindForOs(card.os)] || Icons.server;
  const hueColor = tintFor(hue, 0.70, 0.10);
  const [hover, setHover] = useState(false);

  // '—' is the established no-data placeholder (see tokens.ts's fmtUsage):
  // unknown is never drawn as 0. usage.effective is only ever null when the
  // fleet hasn't reported enough for this device to vouch for a number.
  const tokensValue = usage.effective != null ? fmtK(usage.effective) : '—';
  const tokensSub = usage.partial === true ? 'effective · partial' : 'effective';
  const tokensSubColor = usage.partial === true ? RT.amber : undefined;

  // lastActivity mapping. An unreachable device (card.online false) has
  // no loadPct/sessions reading to derive this from: overview.py reports
  // both null for a device it cannot reach, never a fabricated 0/0 that
  // would otherwise read as a confirmed "idle" here. Leave the sub-label
  // off rather than asserting an activity state we don't actually know.
  const lastActivity = !card.online ? undefined
    : (card.loadPct ?? 0) > 0 ? 'just now'
    : (card.sessions ?? 0) > 0 ? 'active'
    : 'idle';

  return (
    <div
      onClick={onClick}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        cursor: 'pointer',
        background: hover ? RT.cardHi : RT.card,
        border: `1px solid ${hover ? tintEdge(hue) : RT.border}`,
        borderRadius: 12, padding: mobile ? 14 : 18,
        display: 'flex', flexDirection: 'column', gap: mobile ? 12 : 14,
        transition: 'border-color .12s, background .12s',
        opacity: card.online ? 1 : 0.7,
        position: 'relative',
        minWidth: 0, overflow: 'hidden',
      }}
    >
      {/* Left accent bar */}
      <div style={{
        position: 'absolute', left: 0, top: 14, bottom: 14,
        width: 2, background: hueColor, borderRadius: 2, opacity: 0.7,
      }} />

      {/* Header: icon + name + online dot + chevron */}
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12, minWidth: 0 }}>
        <div style={{
          width: mobile ? 34 : 38, height: mobile ? 34 : 38, borderRadius: 9, flex: 'none',
          background: tintSoft(hue),
          display: 'flex', alignItems: 'center', justifyContent: 'center',
        }}>
          <KindIcon size={mobile ? 16 : 18} stroke={hueColor} />
        </div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <div style={{
              fontSize: mobile ? 15 : 16, fontWeight: 600, letterSpacing: '-.01em',
              whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', minWidth: 0,
            }}>{card.name}</div>
            <Dot color={card.online ? RT.green : RT.textLow} size={7} pulse={card.online} />
          </div>
          <div style={{
            fontSize: 11.5, color: RT.textLow, fontFamily: FONT_MONO, marginTop: 3,
            whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
          }}>
            {card.hostname}
          </div>
          {card.version && (() => {
            const hubLauncherVersion = cards.find((x) => x.id === 'local')?.version;
            const skewed = hubLauncherVersion && card.version !== hubLauncherVersion;
            return (
              <div style={{
                fontSize: 10, color: skewed ? RT.amber : RT.textLow,
                fontFamily: FONT_MONO, marginTop: 2,
              }}>
                Launcher v{card.version}{skewed ? ` (hub v${hubLauncherVersion})` : ''}
              </div>
            );
          })()}
          {card.claude_version && (() => {
            const hubVersion = cards.find((x) => x.id === 'local')?.claude_version;
            const skewed = hubVersion && card.claude_version !== hubVersion;
            return (
              <div style={{
                fontSize: 10, color: skewed ? RT.amber : RT.textLow,
                fontFamily: FONT_MONO, marginTop: 2,
              }}>
                Claude Code {card.claude_version}
              </div>
            );
          })()}
        </div>
        <Icons.chevRight size={mobile ? 15 : 16} stroke={RT.textLow} />
      </div>

      {/* Stats: Tokens | Sessions | CPU */}
      <div style={{
        display: 'grid',
        gridTemplateColumns: mobile ? '1fr 1fr 1fr' : '1.4fr 1fr 1fr',
        gap: mobile ? 10 : 16,
      }}>
        {/* Round 4: this bar used to be card.loadPct (CPU), drawn directly
            under a token figure with no other meaning attached. Read as
            token capacity, which nobody intended and a viewer would
            reasonably believe. CPU already has its own stat two columns
            over; this one carries no bar at all now rather than a
            borrowed one. */}
        <V5Stat
          label="Tokens"
          value={tokensValue}
          sub={tokensSub}
          subColor={tokensSubColor}
        />
        <V5Stat
          label="Sessions"
          value={card.sessions ?? '—'}
          sub="active"
        />
        <V5Stat
          label="CPU"
          value={card.loadPct != null ? `${card.loadPct}%` : '—'}
          sub={lastActivity}
        />
      </div>
    </div>
  );
}
