// DeviceDetail.tsx — V5 main-area device detail (hero + launcher + tabs + body).
import { useState } from 'react';
import { RT, FONT_MONO, hueForId } from '../tokens';
import { DeviceHero } from './DeviceHero';
import { V5Launcher } from './V5Launcher';
import { PanelTabs } from './PanelTabs';
import type { PanelTab } from './PanelTabs';
import { SessionRow } from './SessionRow';
import { ScheduledRow } from './ScheduledRow';
import { ScheduleModal } from './ScheduleModal';
import { Logs } from './Logs';
import { PreviewModal } from './PreviewModal';
import { ResumeList } from './ResumeList';
import { usePanelData } from '../usePanelData';
import type { DeviceCard, Schedule } from '../types';
import type { Layout } from '../useLayout';

export interface DeviceDetailProps {
  device: DeviceCard;
  tab: PanelTab;
  setTab: (t: PanelTab) => void;
  onClose: () => void;
  layout: Layout;
}

export function DeviceDetail({ device, tab, setTab, onClose, layout }: DeviceDetailProps) {
  const hue = hueForId(device.id);
  const { sessions, scheduled, reloadSessions, reloadSchedules } = usePanelData(device.id, tab);

  const [modalOpen, setModalOpen]   = useState(false);
  const [editing, setEditing]       = useState<Schedule | null>(null);
  const [previewName, setPreviewName] = useState<string | null>(null);
  const [resumeOpen, setResumeOpen] = useState(false);

  const mobile = layout.mobile;

  function openCreate() { setEditing(null); setModalOpen(true); }
  function openEdit(s: Schedule) { setEditing(s); setModalOpen(true); }
  function closeModal() { setModalOpen(false); setEditing(null); }
  function handleSaved() { reloadSchedules(); closeModal(); }

  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden', minHeight: 0 }}>
      <DeviceHero
        device={device}
        mobile={mobile}
        onClose={onClose}
        onStopAllDone={reloadSessions}
      />

      <V5Launcher
        deviceId={device.id}
        deviceName={device.name}
        mobile={mobile}
        onLaunched={() => { reloadSessions(); setTab('running'); }}
      />

      <PanelTabs
        tab={tab}
        setTab={setTab}
        sessionCount={sessions.length}
        scheduledCount={scheduled.length}
        onResume={() => setResumeOpen(true)}
        mobile={mobile}
      />

      {/* Body */}
      <div style={{
        flex: 1, overflow: 'auto',
        padding: mobile ? 12 : '16px 20px',
        background: RT.bg,
      }}>
        {tab === 'running' && (
          <>
            {/* "+ New schedule" equivalent for sessions: just the list */}
            {sessions.length === 0 ? (
              <V5Empty text={device.online ? `No active sessions on ${device.name}. Launch one above.` : 'Device offline.'} />
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                {sessions.map((s) => (
                  <SessionRow
                    key={s.name}
                    s={s}
                    hue={hue}
                    deviceId={device.id}
                    mobile={mobile}
                    onChanged={reloadSessions}
                    onPreview={(name) => setPreviewName(name)}
                  />
                ))}
              </div>
            )}
          </>
        )}

        {tab === 'scheduled' && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {/* New schedule button */}
            <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 4 }}>
              <button
                onClick={openCreate}
                style={{
                  background: RT.panel, border: `1px solid ${RT.border}`, borderRadius: 6,
                  padding: '7px 12px', color: RT.text, fontSize: 12, fontWeight: 500,
                  cursor: 'pointer', fontFamily: 'inherit',
                  display: 'inline-flex', alignItems: 'center', gap: 5,
                }}
              >
                + New schedule
              </button>
            </div>

            {scheduled.length === 0 ? (
              <V5Empty text="No scheduled tasks on this device." />
            ) : (
              scheduled.map((s) => (
                <ScheduledRow
                  key={s.id}
                  s={s}
                  deviceId={device.id}
                  mobile={mobile}
                  onChanged={reloadSchedules}
                  onEdit={openEdit}
                />
              ))
            )}
          </div>
        )}

        {tab === 'logs' && (
          <div style={{
            background: RT.card, border: `1px solid ${RT.border}`,
            borderRadius: 10, padding: 16,
          }}>
            <Logs device={device} />
          </div>
        )}

        {tab === 'settings' && (
          <V5Empty text="Device settings coming soon." />
        )}
      </div>

      {/* Modals */}
      {modalOpen && (
        <ScheduleModal
          deviceId={device.id}
          initial={editing}
          onClose={closeModal}
          onSaved={handleSaved}
        />
      )}
      {previewName !== null && (
        <PreviewModal
          deviceId={device.id}
          name={previewName}
          onClose={() => setPreviewName(null)}
        />
      )}
      {resumeOpen && (
        <ResumeList
          deviceId={device.id}
          onClose={() => setResumeOpen(false)}
          onResumed={reloadSessions}
        />
      )}
    </div>
  );
}

function V5Empty({ text }: { text: string }) {
  return (
    <div style={{
      padding: 60, textAlign: 'center', color: RT.textLow, fontSize: 13,
      border: `1px dashed ${RT.border}`, borderRadius: 10, background: RT.card,
      fontFamily: FONT_MONO,
    }}>
      {text}
    </div>
  );
}
