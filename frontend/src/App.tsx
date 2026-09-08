// App.tsx — V5 root shell: left rail + main-area detail, or big-card overview grid.
import { useState, useEffect, useCallback, useMemo } from 'react';
import { RT, FONT_SANS, FONT_MONO, fmtK, deviceEffectiveTokens, usagePartialFor } from './tokens';
import { useLayout } from './useLayout';
import { useFleet } from './hooks/useFleet';
import { api } from './api';
import type { DeviceCard, DeviceUsage } from './types';
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
import { ConfigMatrixView } from './components/ConfigMatrix';
import { Activity } from './components/Activity';
import { ShareTunnel } from './components/ShareTunnel';
import { CostView } from './components/CostView';
import { LimitsSummary } from './components/LimitsSummary';
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
  const [desktopView, setDesktopView] = useState<'devices' | 'tasks' | 'sessions' | 'cost' | 'config'>('devices');

  const pickMTab = (t: MTab) => {
    setMTab(t);
    if (t !== 'devices') setOpenId(null);
    localStorage.setItem('rc_mtab', t);
  };

  // hasLoadedCards distinguishes "confirmed zero devices" from "/rc/overview
  // just hasn't answered yet" — cards.length is 0 in both cases, and
  // reading the latter as the former was producing a fabricated "0" on the
  // Effective-tokens aggregate during the brief window after mount where
  // useFleet's SSE had already connected (fleetLoaded true) but this
  // separate /rc/overview poll had not resolved once yet.
  const [hasLoadedCards, setHasLoadedCards] = useState(false);

  const loadOverview = useCallback(async () => {
    try {
      const data = await api.overview();
      if (data?.devices) {
        setCards(data.devices as DeviceCard[]);
      }
      setHasLoadedCards(true);
    } catch {
      // Network error — keep existing cards; hasLoadedCards stays whatever
      // it already was rather than being forced true on a failed call.
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

  // Single fleet subscription for the whole app shell — BigCard/DeviceHero
  // both need live per-session usage, and calling useFleet() once here
  // (rather than once per rendered card) avoids opening a redundant SSE
  // connection per device tile.
  const { devices: fleetDevices, sessions: fleetSessions, connected, usingFallback } = useFleet();
  // Mirrors CostView.tsx's own fleetLoaded computation: before the fleet
  // has reported at all (no SSE frame yet, no fallback poll response yet),
  // an empty sessions array means "we haven't heard," not "confirmed zero
  // sessions" — reading it as the latter would render a lying 0 during the
  // loading window, the exact failure mode this round is about removing.
  const fleetLoaded = connected || usingFallback || fleetDevices.length > 0 || fleetSessions.length > 0;
  const usageByDevice = useMemo<Map<string, DeviceUsage>>(() => {
    const m = new Map<string, DeviceUsage>();
    for (const c of cards) {
      m.set(c.id, {
        effective: fleetLoaded ? deviceEffectiveTokens(c.id, fleetSessions) : null,
        partial: fleetLoaded ? usagePartialFor(fleetDevices, c.id) : undefined,
      });
    }
    return m;
  }, [cards, fleetSessions, fleetDevices, fleetLoaded]);

  // Aggregate effective tokens across devices we can vouch for. Unknown
  // (null) devices are left out of the sum rather than treated as 0, and
  // the whole aggregate is a placeholder rather than 0 whenever there is
  // at least one card but not a single device has vouched for a number —
  // an undercounted-but-confident-looking total is exactly the wrong
  // failure mode here, one register up from the per-card fix.
  const knownUsages = cards.map((c) => usageByDevice.get(c.id)?.effective).filter((v): v is number => v != null);
  const totalTokens: number | null =
    !fleetLoaded || !hasLoadedCards ? null
    : cards.length === 0 ? 0
    : knownUsages.length === 0 ? null
    : knownUsages.reduce((s, v) => s + v, 0);
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
        layout={layout}
        onRefresh={loadOverview}
      />

      {!layout.mobile && <Strip cards={cards} totalTokens={totalTokens} />}
      {/* Round 3: the mobile equivalent of Strip used to live only inside
          the Devices tab's OverviewGrid — invisible on every other mobile
          tab. Lifted to this same top-level, always-rendered position
          (matching desktop's Strip) so account limits and the fleet
          numbers are reachable without switching tabs, not just glanceable
          when you happen to be looking at Devices. */}
      {layout.mobile && <MobileTopStrip cards={cards} totalTokens={totalTokens} />}

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
                <AllSessions onOpenDevice={handleOpenDevice} />
              )}
              {mTab === 'scheduled' && (
                <AllScheduled cards={cards} />
              )}
              {mTab === 'activity' && (
                <Activity cards={cards} />
              )}
              {mTab === 'cost' && (
                <CostView onOpenDevice={handleOpenDevice} />
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
              usage={usageByDevice.get(openCard.id) ?? { effective: null, partial: undefined }}
            />
          ) : layout.mobile ? (
            // Overview grid — big cards
            <OverviewGrid
              cards={cards}
              layout={layout}
              onOpen={handleOpen}
              usageByDevice={usageByDevice}
            />
          ) : (
            // Desktop "All devices" overview: Devices / Tasks / Sessions / Config.
            <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden', minWidth: 0 }}>
              <div style={{
                flex: 'none', display: 'flex', gap: 4, padding: '10px 24px 0',
                borderBottom: `1px solid ${RT.border}`,
              }}>
                {(['devices', 'tasks', 'sessions', 'cost', 'config'] as const).map((v) => (
                  <button
                    key={v}
                    onClick={() => setDesktopView(v)}
                    style={{
                      background: 'transparent', border: 'none', cursor: 'pointer',
                      padding: '8px 12px', fontFamily: FONT_SANS, fontSize: 13,
                      color: desktopView === v ? RT.text : RT.textLow,
                      borderBottom: `2px solid ${desktopView === v ? RT.text : 'transparent'}`,
                      textTransform: 'capitalize',
                    }}
                  >{v === 'devices' ? 'Devices' : v === 'tasks' ? 'Tasks' : v === 'sessions' ? 'Sessions' : v === 'cost' ? 'Cost' : 'Config'}</button>
                ))}
              </div>
              <div style={{ flex: 1, overflow: 'hidden', display: 'flex', minHeight: 0 }}>
                {desktopView === 'devices' && (
                  <OverviewGrid cards={cards} layout={layout} onOpen={handleOpen} usageByDevice={usageByDevice} />
                )}
                {desktopView === 'tasks' && <AllScheduled cards={cards} />}
                {desktopView === 'sessions' && (
                  <AllSessions onOpenDevice={handleOpenDevice} />
                )}
                {desktopView === 'cost' && (
                  <CostView onOpenDevice={handleOpenDevice} />
                )}
                {desktopView === 'config' && <ConfigMatrixView cards={cards} />}
              </div>
            </div>
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
  usageByDevice: Map<string, DeviceUsage>;
}

import type { Layout } from './useLayout';

// Mobile equivalent of Strip.tsx, now rendered at the same always-visible
// top level (see App() above) rather than nested inside the Devices tab's
// OverviewGrid. Round 3: dropped the Load cell (same reasoning as
// Strip.tsx) and added a LimitsSummary row — account limits, not CPU load,
// is what the user asked to have visible "like the status bar".
function MobileTopStrip({ cards, totalTokens }: { cards: DeviceCard[]; totalTokens: number | null }) {
  const onlineCount = cards.filter((c) => c.online).length;
  const totalSessions = cards.reduce((s, c) => s + c.sessions, 0);

  type MCell = { label: string; value: string; dot?: string };
  const cells: MCell[] = [
    { label: 'Online',    value: `${onlineCount}/${cards.length}`, dot: RT.green },
    { label: 'Sessions',  value: String(totalSessions) },
    { label: 'Effective', value: totalTokens != null ? fmtK(totalTokens) : '—' },
  ];

  return (
    <div style={{
      flex: 'none', margin: '10px 12px', background: RT.card,
      border: `1px solid ${RT.border}`, borderRadius: 10, padding: 12,
    }}>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 8 }}>
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
      <div style={{ height: 1, background: RT.border, margin: '11px 0 9px' }} />
      <LimitsSummary mobile />
    </div>
  );
}

function OverviewGrid({ cards, layout, onOpen, usageByDevice }: OverviewGridProps) {
  const n = cards.length;
  const cols = layout.mobile ? 1 : layout.tablet ? Math.min(2, n) : Math.min(3, n);

  return (
    <div style={{ flex: 1, overflow: 'auto', padding: layout.mobile ? 14 : 24 }}>
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
            <BigCard
              key={c.id}
              card={c}
              cards={cards}
              mobile={layout.mobile}
              onClick={() => onOpen(c.id)}
              usage={usageByDevice.get(c.id) ?? { effective: null, partial: undefined }}
            />
          ))}
        </div>
      )}
    </div>
  );
}
