// SidePanel.tsx — RSidePanel + RPanelHeader + shared PanelContent.
// Ported from variant-ops-refined.jsx lines 393-409, 442-464.
import { useState, useEffect, useCallback } from 'react';
import { RT, FONT_MONO, tintFor, tintSoft, hueForId } from '../tokens';
import { Icons, Dot, StatusPill as _StatusPill } from './primitives';
import { btn } from './btn';
import { PanelTabs } from './PanelTabs';
import type { PanelTab } from './PanelTabs';
import { SessionRow } from './SessionRow';
import { ScheduledRow } from './ScheduledRow';
import { Logs } from './Logs';
import { MiniLauncher } from './MiniLauncher';
import { api } from '../api';
import type { DeviceCard, Session, Schedule } from '../types';
import type { Layout } from '../useLayout';

// Suppress unused import warning — StatusPill is used transitively via primitives
void _StatusPill;

// ─── usePanelData ─────────────────────────────────────────────────────────────
// Shared data-fetching hook used by both SidePanel and MobileDetail.
function usePanelData(deviceId: string, tab: PanelTab) {
  const [sessions, setSessions]     = useState<Session[]>([]);
  const [scheduled, setScheduled]   = useState<Schedule[]>([]);

  // Fetch sessions — always, polled every 4 s while open.
  const fetchSessions = useCallback(async () => {
    try {
      const data = await api.sessions(deviceId);
      // /rc/sessions may return array directly or { sessions: [...] }
      const arr: Session[] = Array.isArray(data) ? data : (data?.sessions ?? []);
      setSessions(arr);
    } catch {/* ignore */}
  }, [deviceId]);

  // Fetch schedules — on open + when switching to scheduled tab.
  const fetchScheduled = useCallback(async () => {
    try {
      const data = await api.schedules(deviceId);
      const arr: Schedule[] = Array.isArray(data) ? data : (data?.schedules ?? []);
      setScheduled(arr);
    } catch {/* ignore */}
  }, [deviceId]);

  // Poll sessions every 4 s.
  useEffect(() => {
    let cancelled = false;
    fetchSessions();
    const id = setInterval(() => { if (!cancelled) fetchSessions(); }, 4000);
    return () => { cancelled = true; clearInterval(id); };
  }, [fetchSessions]);

  // Fetch schedules on open + when tab switches to scheduled.
  useEffect(() => {
    if (tab === 'scheduled') fetchScheduled();
  }, [tab, fetchScheduled]);

  return { sessions, scheduled, reloadSessions: fetchSessions };
}

// ─── PanelContent ─────────────────────────────────────────────────────────────
// Shared body used by both SidePanel and MobileDetail.
export interface PanelContentProps {
  device: DeviceCard;
  tab: PanelTab;
  setTab: (t: PanelTab) => void;
  mobile?: boolean;
}

export function PanelContent({ device, tab, setTab, mobile = false }: PanelContentProps) {
  const hue = hueForId(device.id);
  const { sessions, scheduled, reloadSessions } = usePanelData(device.id, tab);

  return (
    <>
      <MiniLauncher
        deviceId={device.id}
        deviceName={device.name}
        mobile={mobile}
        onLaunched={() => {
          reloadSessions();
          setTab('running');
        }}
      />

      <PanelTabs
        tab={tab}
        setTab={setTab}
        sessionCount={sessions.length}
        scheduledCount={scheduled.length}
      />

      {/* Panel body */}
      <div style={{ flex: 1, overflow: 'auto', padding: '10px 12px' }}>
        {tab === 'running' && (
          sessions.length === 0
            ? (
              <div style={{ padding: 40, textAlign: 'center', color: RT.textLow, fontSize: 12 }}>
                {device.online ? 'No active sessions.' : 'Device offline.'}
              </div>
            )
            : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                {sessions.map((s) => (
                  <SessionRow
                    key={s.name}
                    s={s}
                    hue={hue}
                    deviceId={device.id}
                    onChanged={reloadSessions}
                  />
                ))}
              </div>
            )
        )}

        {tab === 'scheduled' && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {scheduled.length === 0
              ? (
                <div style={{ padding: 40, textAlign: 'center', color: RT.textLow, fontSize: 12 }}>
                  No scheduled tasks.
                </div>
              )
              : scheduled.map((s) => (
                <ScheduledRow key={s.id} s={s} />
              ))}
          </div>
        )}

        {tab === 'logs' && <Logs device={device} />}
      </div>
    </>
  );
}

// ─── PanelHeader (desktop) ────────────────────────────────────────────────────
// Ported from variant-ops-refined.jsx lines 442-464.
interface PanelHeaderProps {
  device: DeviceCard;
  onClose: () => void;
}

function PanelHeader({ device, onClose }: PanelHeaderProps) {
  const hue = hueForId(device.id);
  const hueColor = tintFor(hue, 0.70, 0.10);
  const KindIcon = Icons.server; // kindForOs is in Grid; keep SidePanel self-contained

  return (
    <div style={{
      flex: 'none',
      padding: '14px 16px',
      borderBottom: `1px solid ${RT.border}`,
      display: 'flex',
      alignItems: 'flex-start',
      gap: 12,
    }}>
      {/* Device icon badge */}
      <div style={{
        width: 32,
        height: 32,
        borderRadius: 8,
        flex: 'none',
        background: tintSoft(hue),
        color: hueColor,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
      }}>
        <KindIcon size={16} stroke={hueColor} />
      </div>

      {/* Name + hostname */}
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <div style={{ fontSize: 14, fontWeight: 600, letterSpacing: '-.005em' }}>
            {device.name}
          </div>
          <Dot color={device.online ? RT.green : RT.textLow} size={6} pulse={device.online} />
        </div>
        <div style={{ fontSize: 10, color: RT.textLow, fontFamily: FONT_MONO, marginTop: 2 }}>
          {device.hostname}
        </div>
      </div>

      {/* Close button */}
      <button
        onClick={onClose}
        style={{ ...btn('icon'), width: 24, height: 24, color: RT.textDim, fontSize: 12 }}
      >
        ✕
      </button>
    </div>
  );
}

// ─── SidePanel (desktop) ──────────────────────────────────────────────────────
// Ported from variant-ops-refined.jsx lines 393-409.
export interface SidePanelProps {
  device: DeviceCard;
  layout: Layout;
  tab: PanelTab;
  setTab: (t: PanelTab) => void;
  onClose: () => void;
}

export function SidePanel({ device, layout, tab, setTab, onClose }: SidePanelProps) {
  return (
    <div style={{
      flex: 'none',
      width: layout.tablet ? 360 : 420,
      borderLeft: `1px solid ${RT.border}`,
      background: RT.bgRaised,
      display: 'flex',
      flexDirection: 'column',
      overflow: 'hidden',
    }}>
      <PanelHeader device={device} onClose={onClose} />
      <PanelContent device={device} tab={tab} setTab={setTab} />
    </div>
  );
}
