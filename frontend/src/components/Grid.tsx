// Grid.tsx — RGrid + RMobileStrip + RDeviceCard.
// Ported from variant-ops-refined.jsx lines 269-390.
import { useState } from 'react';
import type { DeviceCard } from '../types';
import type { Layout } from '../useLayout';
import { RT, FONT_MONO, FONT_SANS, tintFor, tintSoft, tintEdge, hueForId, fmtK } from '../tokens';
import { Dot, Sparkline, CapBar, Icons } from './primitives';
import { btn } from './btn';

// ─── Props ───────────────────────────────────────────────────────────────────

interface GridProps {
  cards: DeviceCard[];
  layout: Layout;
  openId: string | null;
  onOpen: (id: string) => void;
}

interface DeviceCardProps {
  card: DeviceCard;
  layout: Layout;
  active: boolean;
  onOpen: () => void;
}

interface MobileStripProps {
  cards: DeviceCard[];
}

// ─── os → icon kind helper ───────────────────────────────────────────────────

function kindForOs(os: string): keyof typeof Icons {
  if (/mac/i.test(os)) return 'laptop';
  if (/ubuntu|debian|linux|pop|raspbian/i.test(os)) return 'server';
  return 'server';
}

// ─── RMobileStrip ────────────────────────────────────────────────────────────

function MobileStrip({ cards }: MobileStripProps) {
  const onlineCount = cards.filter((c) => c.online).length;
  const totalSessions = cards.reduce((s, c) => s + c.sessions, 0);
  const totalTokens = cards.reduce((s, c) => s + c.tokens, 0);
  const onlineCards = cards.filter((c) => c.online);
  const avgLoad = onlineCards.length > 0
    ? Math.round(onlineCards.reduce((s, c) => s + c.loadPct, 0) / onlineCards.length)
    : 0;

  type MCell = { label: string; value: string; dot?: string };
  const cells: MCell[] = [
    { label: 'Online',  value: `${onlineCount}/${cards.length}`, dot: RT.green },
    { label: 'Sessns',  value: String(totalSessions) },
    { label: 'Tokens',  value: fmtK(totalTokens) },
    { label: 'Load',    value: `${avgLoad}%` },
  ];

  return (
    <div style={{
      display: 'grid',
      gridTemplateColumns: 'repeat(4, 1fr)',
      gap: 8,
      marginBottom: 14,
      background: RT.card,
      border: `1px solid ${RT.border}`,
      borderRadius: 10,
      padding: 12,
    }}>
      {cells.map((c) => (
        <div key={c.label}>
          <div style={{ fontSize: 8, color: RT.textLow, letterSpacing: '.14em', textTransform: 'uppercase', fontFamily: FONT_MONO }}>{c.label}</div>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 4, marginTop: 3 }}>
            {c.dot && <Dot color={c.dot} size={5} pulse />}
            <div style={{ fontFamily: FONT_MONO, fontSize: 14, fontWeight: 500 }}>{c.value}</div>
          </div>
        </div>
      ))}
    </div>
  );
}

// ─── RDeviceCard ─────────────────────────────────────────────────────────────

function DeviceCardItem({ card, layout, active, onOpen }: DeviceCardProps) {
  const hue = hueForId(card.id);
  const KindIcon = Icons[kindForOs(card.os)] || Icons.server;
  const hueColor = tintFor(hue, 0.66, 0.08);
  const hueAccent = tintFor(hue, 0.74, 0.11);
  const [hover, setHover] = useState(false);

  return (
    <div
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      onClick={onOpen}
      style={{
        cursor: 'pointer',
        background: active ? RT.cardHi : RT.card,
        border: `1px solid ${active ? tintEdge(hue) : (hover ? RT.borderHi : RT.border)}`,
        borderRadius: 10,
        padding: layout.mobile ? '14px 14px 12px' : '13px 14px 12px',
        position: 'relative',
        overflow: 'hidden',
        display: 'flex',
        flexDirection: 'column',
        gap: 10,
        transition: 'border-color .12s, background .12s',
        opacity: card.online ? 1 : 0.65,
      }}
    >
      {/* Subtle left edge accent */}
      <div style={{
        position: 'absolute',
        top: 10,
        bottom: 10,
        left: 0,
        width: 2,
        background: hueColor,
        borderRadius: 2,
        opacity: active ? 1 : 0.6,
      }} />

      {/* Row 1: icon + name + online dot */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <div style={{
          width: 28,
          height: 28,
          borderRadius: 7,
          flex: 'none',
          background: tintSoft(hue),
          color: hueAccent,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
        }}>
          <KindIcon size={14} stroke={hueAccent} />
        </div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
            <div style={{ fontSize: 13, fontWeight: 600, letterSpacing: '-.005em', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{card.name}</div>
            <Dot color={card.online ? RT.green : RT.textLow} size={6} pulse={card.online} />
          </div>
          <div style={{ fontSize: 10, color: RT.textLow, fontFamily: FONT_MONO, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', marginTop: 2 }}>{card.hostname || card.id}</div>
        </div>
      </div>

      {/* Meta row: os + load/offline (replaces region + lastActivity) */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontFamily: FONT_MONO, fontSize: 10, color: RT.textDim, flexWrap: 'wrap' }}>
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 3 }}>
          <Icons.globe size={9} stroke={RT.textLow} /> {card.os || 'unknown'}
        </span>
        <span style={{ color: RT.textLow }}>·</span>
        <span>{card.online ? `${card.loadPct}% load` : 'offline'}</span>
      </div>

      {/* Sparkline + tokens */}
      <div style={{ display: 'flex', alignItems: 'flex-end', gap: 10 }}>
        <div style={{ flex: 1, color: hueColor }}>
          <Sparkline data={card.spark} w={200} h={28} color={hueColor} fillOpacity={0.10} dotEnd />
        </div>
        <div style={{ textAlign: 'right' }}>
          <div style={{ fontFamily: FONT_MONO, fontSize: 14, fontWeight: 500, letterSpacing: '-.01em' }}>{fmtK(card.tokens)}</div>
          <div style={{ fontSize: 9, color: RT.textLow, fontFamily: FONT_MONO, letterSpacing: '.06em' }}>TOKENS</div>
        </div>
      </div>

      {/* CPU load bar + load + sessions (replaces token-cap bar) */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <CapBar pct={card.loadPct} height={3} bg="rgba(255,255,255,.04)" color={hueColor} />
        <div style={{ fontSize: 10, fontFamily: FONT_MONO, color: RT.textDim, whiteSpace: 'nowrap' }}>
          {card.loadPct}% load · {card.sessions} sess
        </div>
      </div>
    </div>
  );
}

// ─── RGrid ───────────────────────────────────────────────────────────────────

export function Grid({ cards, layout, openId, onOpen }: GridProps) {
  const cols = layout.mobile ? 1 : layout.tablet ? 2 : 3;

  return (
    <div style={{ flex: 1, overflow: 'auto', padding: layout.mobile ? 14 : 18 }}>
      {/* Mobile mini-strip (since full strip is hidden on mobile) */}
      {layout.mobile && <MobileStrip cards={cards} />}

      <div style={{ display: 'flex', alignItems: 'center', marginBottom: 12, gap: 10 }}>
        <div style={{ fontSize: 10, color: RT.textDim, letterSpacing: '.14em', textTransform: 'uppercase', fontFamily: FONT_MONO }}>
          Devices · {cards.length}
        </div>
        {!layout.mobile && (
          <div style={{ fontSize: 10, color: RT.textLow, fontFamily: FONT_MONO }}>sorted by activity</div>
        )}
        <div style={{ flex: 1 }} />
        <button style={{ ...btn('tinyText'), fontFamily: FONT_SANS }}>{layout.mobile ? '+' : '+ Add'}</button>
      </div>

      {cards.length === 0 ? (
        <div style={{ padding: 40, textAlign: 'center', color: RT.textLow, fontFamily: FONT_MONO, fontSize: 12 }}>loading…</div>
      ) : (
        <div style={{ display: 'grid', gridTemplateColumns: `repeat(${cols}, 1fr)`, gap: layout.mobile ? 10 : 12 }}>
          {cards.map((c) => (
            <DeviceCardItem
              key={c.id}
              card={c}
              layout={layout}
              active={openId === c.id}
              onOpen={() => onOpen(c.id)}
            />
          ))}
        </div>
      )}
    </div>
  );
}
