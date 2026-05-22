// SidePanel.tsx — RSidePanel + RPanelHeader + shared PanelContent.
import { useState, useEffect, useCallback } from 'react';
import { RT, FONT_MONO, tintFor, tintSoft, hueForId, kindForOs } from '../tokens';
import { Icons, Dot } from './primitives';
import { btn } from './btn';
import { PanelTabs } from './PanelTabs';
import type { PanelTab } from './PanelTabs';
import { SessionRow } from './SessionRow';
import { ScheduledRow } from './ScheduledRow';
import { ScheduleModal } from './ScheduleModal';
import { Logs } from './Logs';
import { MiniLauncher } from './MiniLauncher';
import { PreviewModal } from './PreviewModal';
import { ResumeList } from './ResumeList';
import { api } from '../api';
import type { DeviceCard, Session, Schedule } from '../types';
import type { Layout } from '../useLayout';

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

  return { sessions, scheduled, reloadSessions: fetchSessions, reloadSchedules: fetchScheduled };
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
  const { sessions, scheduled, reloadSessions, reloadSchedules } = usePanelData(device.id, tab);

  // Schedule modal state
  const [modalOpen, setModalOpen] = useState(false);
  const [editing,   setEditing]   = useState<Schedule | null>(null);

  // Preview modal state
  const [previewName, setPreviewName] = useState<string | null>(null);

  // Resume list state
  const [resumeOpen, setResumeOpen] = useState(false);

  function openCreate() {
    setEditing(null);
    setModalOpen(true);
  }

  function openEdit(s: Schedule) {
    setEditing(s);
    setModalOpen(true);
  }

  function closeModal() {
    setModalOpen(false);
    setEditing(null);
  }

  function handleSaved() {
    reloadSchedules();
    closeModal();
  }

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
          <>
            {/* Resume button */}
            <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 6 }}>
              <button
                onClick={() => setResumeOpen(true)}
                style={{ ...btn('tinyText'), gap: 5 }}
              >
                <Icons.refresh size={13} stroke="currentColor" />
                Resume…
              </button>
            </div>

            {sessions.length === 0
              ? (
                <div style={{ padding: 40, textAlign: 'center', color: RT.textLow, fontSize: 13 }}>
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
                      onPreview={(name) => setPreviewName(name)}
                    />
                  ))}
                </div>
              )
            }
          </>
        )}

        {tab === 'scheduled' && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {/* "+ New schedule" button */}
            <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 2 }}>
              <button
                onClick={openCreate}
                style={{
                  ...btn('tinyText'),
                  gap: 5,
                }}
              >
                <Icons.plus size={13} stroke="currentColor" />
                New schedule
              </button>
            </div>

            {scheduled.length === 0
              ? (
                <div style={{ padding: 32, textAlign: 'center', color: RT.textLow, fontSize: 13 }}>
                  No scheduled tasks.
                </div>
              )
              : scheduled.map((s) => (
                <ScheduledRow
                  key={s.id}
                  s={s}
                  deviceId={device.id}
                  onChanged={reloadSchedules}
                  onEdit={openEdit}
                />
              ))}
          </div>
        )}

        {tab === 'logs' && <Logs device={device} />}
      </div>

      {/* Schedule create/edit modal */}
      {modalOpen && (
        <ScheduleModal
          deviceId={device.id}
          initial={editing}
          onClose={closeModal}
          onSaved={handleSaved}
        />
      )}

      {/* Preview modal */}
      {previewName !== null && (
        <PreviewModal
          deviceId={device.id}
          name={previewName}
          onClose={() => setPreviewName(null)}
        />
      )}

      {/* Resume list modal */}
      {resumeOpen && (
        <ResumeList
          deviceId={device.id}
          onClose={() => setResumeOpen(false)}
          onResumed={reloadSessions}
        />
      )}
    </>
  );
}

// ─── PanelHeader (desktop) ────────────────────────────────────────────────────
interface PanelHeaderProps {
  device: DeviceCard;
  onClose: () => void;
}

function PanelHeader({ device, onClose }: PanelHeaderProps) {
  const hue = hueForId(device.id);
  const hueColor = tintFor(hue, 0.70, 0.10);
  const KindIcon = Icons[kindForOs(device.os)] || Icons.server;

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
          <div style={{ fontSize: 15, fontWeight: 600, letterSpacing: '-.005em' }}>
            {device.name}
          </div>
          <Dot color={device.online ? RT.green : RT.textLow} size={6} pulse={device.online} />
        </div>
        <div style={{ fontSize: 11, color: RT.textLow, fontFamily: FONT_MONO, marginTop: 2 }}>
          {device.hostname}
        </div>
      </div>

      {/* Close button */}
      <button
        onClick={onClose}
        style={{ ...btn('icon'), width: 28, height: 28, color: RT.textDim, fontSize: 14 }}
      >
        ✕
      </button>
    </div>
  );
}

// ─── SidePanel (desktop) ──────────────────────────────────────────────────────
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
