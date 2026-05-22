// App.tsx — root shell. Owns cards/openId/tab state + overview polling.
// Ported from variant-ops-refined.jsx lines 48-79.
import { useState, useEffect, useCallback } from 'react';
import { RT, FONT_SANS } from './tokens';
import { useLayout } from './useLayout';
import { api } from './api';
import type { DeviceCard } from './types';
import { ensureKeyframes } from './components/primitives';
import { Header } from './components/Header';
import { Strip } from './components/Strip';
import { Grid } from './components/Grid';
import { SidePanel } from './components/SidePanel';
import { MobileDetail } from './components/MobileDetail';
import type { PanelTab } from './components/PanelTabs';

ensureKeyframes();

export function App() {
  const layout = useLayout();
  const [cards, setCards] = useState<DeviceCard[]>([]);
  const [openId, setOpenId] = useState<string | null>(null);
  const [tab, setTab] = useState<PanelTab>('running');

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
    let cancelled = false;

    const load = async () => {
      try {
        const data = await api.overview();
        if (!cancelled && data?.devices) {
          setCards(data.devices as DeviceCard[]);
        }
      } catch {
        // Network error — keep existing cards.
      }
    };

    load();
    const interval = setInterval(load, 5000);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, []);

  // Reset tab when switching device.
  const handleOpen = (id: string | null) => {
    if (id !== openId) setTab('running');
    setOpenId(id);
  };

  const openCard: DeviceCard | undefined = cards.find((c) => c.id === openId);

  const onlineCount = cards.filter((c) => c.online).length;
  const totalTokens = cards.reduce((s, c) => s + c.tokens, 0);

  return (
    <div style={{
      width: '100%',
      height: '100%',
      background: RT.bg,
      color: RT.text,
      fontFamily: FONT_SANS,
      display: 'flex',
      flexDirection: 'column',
      overflow: 'hidden',
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

      <div style={{ flex: 1, display: 'flex', overflow: 'hidden', position: 'relative' }}>
        <Grid cards={cards} layout={layout} openId={openId} onOpen={handleOpen} />

        {/* Side panel — desktop slides from right; mobile becomes full-screen overlay */}
        {openCard && (
          layout.mobile
            ? (
              <MobileDetail
                device={openCard}
                tab={tab}
                setTab={setTab}
                onClose={() => setOpenId(null)}
              />
            )
            : (
              <SidePanel
                device={openCard}
                layout={layout}
                tab={tab}
                setTab={setTab}
                onClose={() => setOpenId(null)}
              />
            )
        )}
      </div>
    </div>
  );
}
