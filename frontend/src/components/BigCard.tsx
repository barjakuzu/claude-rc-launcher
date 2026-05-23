// BigCard.tsx — V5 large rich device card for the overview grid.
import { useState } from 'react';
import { RT, FONT_MONO, tintFor, tintSoft, tintEdge, hueForId, fmtK, kindForOs } from '../tokens';
import { Dot, Sparkline, CapBar, Icons } from './primitives';
import type { DeviceCard } from '../types';

interface BigCardProps {
  card: DeviceCard;
  onClick: () => void;
}

function V5Stat({ label, value, bar, barColor, sub }: {
  label: string; value: string | number;
  bar?: number; barColor?: string; sub?: string;
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
        <div style={{ fontSize: 10, color: RT.textLow, marginTop: 4, fontFamily: FONT_MONO }}>{sub}</div>
      )}
    </div>
  );
}

export function BigCard({ card, onClick }: BigCardProps) {
  const hue = hueForId(card.id);
  const KindIcon = Icons[kindForOs(card.os)] || Icons.server;
  const hueColor = tintFor(hue, 0.70, 0.10);
  const [hover, setHover] = useState(false);

  // lastActivity mapping
  const lastActivity = card.loadPct > 0 ? 'just now' : card.sessions > 0 ? 'active' : 'idle';

  return (
    <div
      onClick={onClick}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        cursor: 'pointer',
        background: hover ? RT.cardHi : RT.card,
        border: `1px solid ${hover ? tintEdge(hue) : RT.border}`,
        borderRadius: 12, padding: 18,
        display: 'flex', flexDirection: 'column', gap: 14,
        transition: 'border-color .12s, background .12s',
        opacity: card.online ? 1 : 0.7,
        position: 'relative',
      }}
    >
      {/* Left accent bar */}
      <div style={{
        position: 'absolute', left: 0, top: 14, bottom: 14,
        width: 2, background: hueColor, borderRadius: 2, opacity: 0.7,
      }} />

      {/* Header: icon + name + online dot + chevron */}
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12 }}>
        <div style={{
          width: 38, height: 38, borderRadius: 9, flex: 'none',
          background: tintSoft(hue),
          display: 'flex', alignItems: 'center', justifyContent: 'center',
        }}>
          <KindIcon size={18} stroke={hueColor} />
        </div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <div style={{ fontSize: 16, fontWeight: 600, letterSpacing: '-.01em' }}>{card.name}</div>
            <Dot color={card.online ? RT.green : RT.textLow} size={7} pulse={card.online} />
          </div>
          <div style={{ fontSize: 11.5, color: RT.textLow, fontFamily: FONT_MONO, marginTop: 3 }}>
            {card.hostname}
          </div>
        </div>
        <Icons.chevRight size={16} stroke={RT.textLow} />
      </div>

      {/* Stats grid: Tokens | Sessions | CPU */}
      <div style={{ display: 'grid', gridTemplateColumns: '1.4fr 1fr 1fr', gap: 16 }}>
        <V5Stat
          label="Tokens"
          value={fmtK(card.tokens)}
          bar={card.loadPct}
          barColor={hueColor}
        />
        <V5Stat
          label="Sessions"
          value={card.sessions}
          sub="active"
        />
        <V5Stat
          label="CPU"
          value={`${card.loadPct}%`}
          sub={lastActivity}
        />
      </div>

      {/* Sparkline + Open button */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <div style={{ flex: 1, color: hueColor }}>
          <Sparkline data={card.spark} w={300} h={32} color={hueColor} fillOpacity={0.10} dotEnd />
        </div>
        <button
          onClick={(e) => { e.stopPropagation(); onClick(); }}
          style={{
            background: RT.panel, color: RT.text,
            border: `1px solid ${RT.border}`,
            borderRadius: 7, padding: '8px 12px', cursor: 'pointer',
            fontFamily: 'inherit', fontSize: 11.5, fontWeight: 500,
            display: 'inline-flex', alignItems: 'center', gap: 6,
          }}
        >
          Open <Icons.chevRight size={11} stroke={RT.text} />
        </button>
      </div>
    </div>
  );
}
