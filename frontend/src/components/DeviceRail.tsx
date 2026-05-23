// DeviceRail.tsx — V5 left rail: compact device switcher (240px).
import { useState } from 'react';
import { RT, FONT_MONO, tintFor, tintSoft, tintEdge, hueForId, kindForOs } from '../tokens';
import { Dot, Icons, CapBar } from './primitives';
import type { DeviceCard } from '../types';

interface DeviceRailProps {
  cards: DeviceCard[];
  openId: string | null;
  setOpenId: (id: string | null) => void;
}

interface RailItemProps {
  card: DeviceCard;
  active: boolean;
  onClick: () => void;
}

function V5RailItem({ card, active, onClick }: RailItemProps) {
  const hue = hueForId(card.id);
  const KindIcon = Icons[kindForOs(card.os)] || Icons.server;
  const hueColor = tintFor(hue, 0.70, 0.10);
  const [hover, setHover] = useState(false);

  return (
    <button
      onClick={onClick}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        width: '100%', textAlign: 'left',
        background: active ? RT.cardHi : hover ? RT.card : 'transparent',
        border: `1px solid ${active ? tintEdge(hue) : 'transparent'}`,
        borderRadius: 8, padding: '9px 10px', cursor: 'pointer',
        color: RT.text, fontFamily: 'inherit',
        display: 'flex', flexDirection: 'column', gap: 6,
        position: 'relative', transition: 'background .1s, border-color .1s',
      }}
    >
      {/* Active left accent bar */}
      {active && (
        <div style={{
          position: 'absolute', left: -1, top: 8, bottom: 8, width: 2,
          background: hueColor, borderRadius: 2,
        }} />
      )}

      {/* Top row: icon + name + online dot */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <div style={{
          width: 22, height: 22, borderRadius: 5, flex: 'none',
          background: tintSoft(hue),
          display: 'flex', alignItems: 'center', justifyContent: 'center',
        }}>
          <KindIcon size={11} stroke={hueColor} />
        </div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{
            fontSize: 12.5, fontWeight: 600, letterSpacing: '-.005em',
            whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
          }}>
            {card.name}
          </div>
        </div>
        <Dot color={card.online ? RT.green : RT.textLow} size={6} pulse={card.online} />
      </div>

      {/* Bottom row: cap bar + sessions count */}
      <div style={{
        display: 'flex', alignItems: 'center', gap: 7,
        fontFamily: FONT_MONO, fontSize: 10, color: RT.textLow,
      }}>
        <CapBar pct={card.loadPct} height={2} bg="rgba(255,255,255,.04)" color={hueColor} />
        <span style={{ whiteSpace: 'nowrap' }}>{card.sessions} sess</span>
      </div>
    </button>
  );
}

export function DeviceRail({ cards, openId, setOpenId }: DeviceRailProps) {
  return (
    <div style={{
      flex: 'none', width: 240,
      borderRight: `1px solid ${RT.border}`,
      background: RT.bgRaised,
      display: 'flex', flexDirection: 'column', overflow: 'hidden',
    }}>
      {/* Header */}
      <div style={{
        flex: 'none', padding: '14px 14px 8px',
        display: 'flex', alignItems: 'center',
      }}>
        <div style={{
          fontSize: 9.5, color: RT.textDim,
          letterSpacing: '.14em', textTransform: 'uppercase', fontFamily: FONT_MONO,
        }}>
          Devices · {cards.length}
        </div>
        <div style={{ flex: 1 }} />
        <button
          title="Add device"
          style={{
            background: RT.panel, border: `1px solid ${RT.border}`, borderRadius: 5,
            width: 22, height: 22, padding: 0, cursor: 'pointer',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
          }}
        >
          <Icons.plus size={11} stroke={RT.textDim} />
        </button>
      </div>

      {/* Device list */}
      <div style={{
        flex: 1, overflow: 'auto',
        padding: '0 8px 10px',
        display: 'flex', flexDirection: 'column', gap: 4,
      }}>
        {cards.map((card) => (
          <V5RailItem
            key={card.id}
            card={card}
            active={openId === card.id}
            onClick={() => setOpenId(card.id)}
          />
        ))}
      </div>

      {/* Back to overview */}
      <button
        onClick={() => setOpenId(null)}
        style={{
          flex: 'none', margin: '0 8px 12px',
          padding: '10px 11px',
          background: 'transparent',
          border: `1px dashed ${RT.border}`,
          borderRadius: 7, color: RT.textDim, fontFamily: 'inherit',
          fontSize: 11.5, cursor: 'pointer',
          display: 'flex', alignItems: 'center', gap: 8,
        }}
      >
        <Icons.back size={11} stroke={RT.textDim} /> Back to overview
      </button>
    </div>
  );
}
