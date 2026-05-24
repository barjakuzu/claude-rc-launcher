// App.tsx — V5 root shell: left rail + main-area detail, or big-card overview grid.
import { useState, useEffect, useCallback } from 'react';
import { RT, FONT_SANS, FONT_MONO, fmtK } from './tokens';
import { useLayout } from './useLayout';
import { api } from './api';
import type { DeviceCard } from './types';
import { ensureKeyframes } from './components/primitives';
import { Header } from './components/Header';
import { Strip } from './components/Strip';
import { DeviceRail } from './components/DeviceRail';
import { DeviceDetail } from './components/DeviceDetail';
import { BigCard } from './components/BigCard';
import { MobileNav } from './components/MobileNav';
import { MobileMoreSheet } from './components/MobileMoreSheet';
import { AllSessions } from './components/AllSessions';
import { AllScheduled } from './components/AllScheduled';
import { Activity } from './components/Activity';
import { ShareTunnel } from './components/ShareTunnel';
import type { PanelTab } from './components/PanelTabs';
import type { MTab } from './components/MobileNav';

ensureKeyframes();

export function App() {
  const layout = useLayout();
  const [cards, setCards] = useState<DeviceCard[]>([]);
  const [openId, setOpenId] = useState<string | null>(null);
  const [tab, setTab] = useState<PanelTab>('running');

  // Mobile tab state — persisted across page loads.
  const [mTab, setMTab] = useState<MTab>(
    () => (localStorage.getItem('rc_mtab') as MTab) ?? 'devices'
  );
  const [moreOpen, setMoreOpen] = useState(false);
  const [shareOpen, setShareOpen] = useState(false);

  const pickMTab = (t: MTab) => {
    setMTab(t);
    if (t !== 'devices') setOpenId(null);
    localStorage.setItem('rc_mtab', t);
  };

  const loadOverview = useCallback(async () => {
    try {
      const data = await api.overview();
      if (data?.devices) {
        setCards(data.devices as DeviceCard[]);
      }
    } catch {
      // Network error — keep existing cards.
    }
  }, []);

  // Poll /rc/overview every 5 seconds.
  useEffect(() => {
    loadOverview();
    const interval = setInterval(loadOverview, 5000);
    return () => { clearInterval(interval); };
  }, [loadOverview]);

  // Reset tab when switching device.
  const handleOpen = (id: string | null) => {
    if (id !== openId) setTab('running');
    setOpenId(id);
  };

  const openCard: DeviceCard | undefined = cards.find((c) => c.id === openId);

  const onlineCount = cards.filter((c) => c.online).length;
  const totalTokens = cards.reduce((s, c) => s + c.tokens, 0);
  const totalSessions = cards.reduce((s, c) => s + c.sessions, 0);

  // Handler for cross-device views that want to open a specific device.
  const handleOpenDevice = (id: string) => {
    handleOpen(id);
    pickMTab('devices');
  };

  return (
    <div style={{
      width: '100%', height: '100%',
      background: RT.bg, color: RT.text,
      fontFamily: FONT_SANS,
      display: 'flex', flexDirection: 'column', overflow: 'hidden',
      position: 'relative',
    }}>
      <Header
        cards={cards}
        openId={openId}
        setOpenId={handleOpen}
        onlineCount={onlineCount}
        totalTokens={totalTokens}
        layout={layout}
        onRefresh={loadOverview}
      />

      {!layout.mobile && <Strip cards={cards} />}

      <div style={{ flex: 1, display: 'flex', overflow: 'hidden', minHeight: 0 }}>
        {/* Left rail: only when a device is open and not mobile */}
        {openCard && !layout.mobile && (
          <DeviceRail cards={cards} openId={openId} setOpenId={handleOpen} />
        )}

        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden', minWidth: 0 }}>
          {layout.mobile && mTab !== 'devices' ? (
            // Mobile cross-device tab views.
            <>
              {mTab === 'sessions' && (
                <AllSessions cards={cards} onOpenDevice={handleOpenDevice} />
              )}
              {mTab === 'scheduled' && (
                <AllScheduled cards={cards} />
              )}
              {mTab === 'activity' && (
                <Activity cards={cards} />
              )}
            </>
          ) : openCard ? (
            // Device detail — full main area
            <DeviceDetail
              device={openCard}
              cards={cards}
              tab={tab}
              setTab={setTab}
              onClose={() => handleOpen(null)}
              layout={layout}
            />
          ) : (
            // Overview grid — big cards
            <OverviewGrid
              cards={cards}
              layout={layout}
              onOpen={handleOpen}
            />
          )}
        </div>
      </div>

      {/* Footer / mobile nav */}
      {layout.mobile ? (
        <>
          <MobileMoreSheet
            open={moreOpen}
            onClose={() => setMoreOpen(false)}
            openId={openId}
            cards={cards}
            setOpenId={setOpenId}
            setMTab={pickMTab}
            setDeviceTab={setTab}
            onShareOpen={() => setShareOpen(true)}
          />
          <MobileNav
            active={mTab}
            onChange={pickMTab}
            onMore={() => setMoreOpen(true)}
            moreOpen={moreOpen}
            counts={{ devices: cards.length, sessions: totalSessions, scheduled: 0 }}
          />
        </>
      ) : null}

      {/* Share tunnel modal — reachable from More sheet */}
      {shareOpen && <ShareTunnel onClose={() => setShareOpen(false)} />}
    </div>
  );
}

// ─── Overview grid ────────────────────────────────────────────────────────────

interface OverviewGridProps {
  cards: DeviceCard[];
  layout: Layout;
  onOpen: (id: string) => void;
}

import type { Layout } from './useLayout';

function MobileStrip({ cards }: { cards: DeviceCard[] }) {
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
      display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 8,
      marginBottom: 14, background: RT.card,
      border: `1px solid ${RT.border}`, borderRadius: 10, padding: 12,
    }}>
      {cells.map((c) => (
        <div key={c.label}>
          <div style={{ fontSize: 8, color: RT.textLow, letterSpacing: '.14em', textTransform: 'uppercase', fontFamily: FONT_MONO }}>{c.label}</div>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 4, marginTop: 3 }}>
            {c.dot && <span style={{ width: 5, height: 5, borderRadius: 5, background: c.dot, display: 'inline-block' }} />}
            <div style={{ fontFamily: FONT_MONO, fontSize: 15, fontWeight: 500 }}>{c.value}</div>
          </div>
        </div>
      ))}
    </div>
  );
}

function OverviewGrid({ cards, layout, onOpen }: OverviewGridProps) {
  const n = cards.length;
  const cols = layout.mobile ? 1 : layout.tablet ? Math.min(2, n) : Math.min(3, n);

  return (
    <div style={{ flex: 1, overflow: 'auto', padding: layout.mobile ? 14 : 24 }}>
      {/* Mobile mini-strip */}
      {layout.mobile && <MobileStrip cards={cards} />}

      <div style={{ display: 'flex', alignItems: 'baseline', marginBottom: 16, gap: 10 }}>
        <div style={{
          fontSize: 11, color: RT.textDim, letterSpacing: '.14em',
          textTransform: 'uppercase', fontFamily: FONT_MONO,
        }}>
          Devices · {n}
        </div>
        {!layout.mobile && (
          <div style={{ fontSize: 11, color: RT.textLow, fontFamily: FONT_MONO }}>
            sorted by activity
          </div>
        )}
        <div style={{ flex: 1 }} />
      </div>

      {n === 0 ? (
        <div style={{ padding: 40, textAlign: 'center', color: RT.textLow, fontFamily: FONT_MONO, fontSize: 13 }}>
          loading…
        </div>
      ) : (
        <div style={{
          display: 'grid',
          gridTemplateColumns: `repeat(${cols}, 1fr)`,
          gap: 14,
          maxWidth: cols === 2 ? 1200 : 'none',
        }}>
          {cards.map((c) => (
            <BigCard key={c.id} card={c} mobile={layout.mobile} onClick={() => onOpen(c.id)} />
          ))}
        </div>
      )}
    </div>
  );
}
