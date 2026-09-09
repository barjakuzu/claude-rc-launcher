// DeviceDetail.tsx — V5 main-area device detail (hero + launcher + tabs + body).
import { useState } from 'react';
import { RT, FONT_MONO, hueForId, withAlpha } from '../tokens';
import { DeviceHero } from './DeviceHero';
import { V5Launcher } from './V5Launcher';
import { PanelTabs } from './PanelTabs';
import type { PanelTab } from './PanelTabs';
import { SessionRow } from './SessionRow';
import { ScheduledRow } from './ScheduledRow';
import { ScheduleModal } from './ScheduleModal';
import { Logs } from './Logs';
import { DeviceSettings } from './DeviceSettings';
import { PreviewModal } from './PreviewModal';
import { ResumeList } from './ResumeList';
import { usePanelData } from '../usePanelData';
import type { DeviceCard, DeviceUsage, Schedule } from '../types';
import type { Layout } from '../useLayout';

export interface DeviceDetailProps {
  device: DeviceCard;
  cards: DeviceCard[];
  tab: PanelTab;
  setTab: (t: PanelTab) => void;
  onClose: () => void;
  layout: Layout;
  /** Live effective-token reading for this device (App.tsx). */
  usage: DeviceUsage;
}

export function DeviceDetail({ device, cards, tab, setTab, onClose, layout, usage }: DeviceDetailProps) {
  const hue = hueForId(device.id);
  const {
    sessions, scheduled,
    hasLoadedSessions, hasLoadedScheduled,
    sessionsUnreachable, scheduledLoadError,
    reloadSessions, reloadSchedules,
  } = usePanelData(device.id, tab);

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
        cards={cards}
        mobile={mobile}
        onClose={onClose}
        onStopAllDone={reloadSessions}
        usage={usage}
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
        sessionCount={hasLoadedSessions ? sessions.length : null}
        scheduledCount={hasLoadedScheduled ? scheduled.length : null}
        onResume={() => setResumeOpen(true)}
        mobile={mobile}
      />

      {/* Body */}
      <div style={{
        flex: 1, overflow: 'auto',
        padding: mobile ? '12px 12px 32px' : '16px 20px',
        background: RT.bg,
      }}>
        {tab === 'running' && !mobile && (
          <>
            {/* "+ New schedule" equivalent for sessions: just the list.
                Mobile hides this — the Sessions tab (AllSessions.tsx)
                already lists every device's sessions, so repeating just
                this device's here would mean scrolling past the same
                rows twice on a phone screen. */}
            {sessions.length === 0 ? (
              <V5Empty text={
                !hasLoadedSessions ? 'Loading sessions…'
                // Round 4: this used to defer to device.online, from the
                // separate, staler /rc/overview poll. usePanelData's own
                // direct /rc/sessions probe is fresher and more
                // authoritative for this exact question, and a successful
                // fetch (hasLoadedSessions true, sessionsUnreachable
                // false) already proves the device answered, so it wins
                // outright rather than being cross-checked against a
                // second opinion that can lag behind it in either
                // direction.
                : sessionsUnreachable ? 'Device offline.'
                : `No active sessions on ${device.name}. Launch one above.`
              } />
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                {sessions.map((s) => (
                  <SessionRow
                    key={s.kind === 'external' ? 'ext:' + (s.session_id ?? s.sessionId ?? s.name) : (s.session_id ?? s.sessionId ?? s.name)}
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

        {tab === 'running' && mobile && (
          <div style={{ fontFamily: FONT_MONO, fontSize: 11.5, color: RT.textLow, padding: '4px 2px' }}>
            See the Sessions tab for this device's sessions.
          </div>
        )}

        {tab === 'scheduled' && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {/* Round 4: GET /schedules answers 200 with an "error" field
                (schedules.LAST_LOAD_ERROR) when this device's own
                schedules.json failed to parse or dropped invalid entries,
                and load_schedules() keeps whatever validated (possibly
                non-empty), so this can't-be-fully-trusted state isn't
                limited to the empty-list case below. Same treatment
                AlertsIndicator.tsx's config_error banner already gives a
                broken guard.json: surfaced unconditionally, not folded
                into the empty-state text, since a real (but possibly
                incomplete) list still needs the same caveat. */}
            {scheduledLoadError && (
              <div style={{
                padding: '8px 9px', borderRadius: 6,
                background: withAlpha(RT.amber, 0.12), border: `1px solid ${withAlpha(RT.amber, 0.4)}`,
                fontSize: 11.5, color: RT.amber, lineHeight: 1.4, fontFamily: FONT_MONO,
              }}>
                Schedules file has a problem: {scheduledLoadError}. The list below may be incomplete.
              </div>
            )}
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
              <V5Empty text={hasLoadedScheduled ? 'No scheduled tasks on this device.' : 'Loading scheduled tasks…'} />
            ) : (
              scheduled.map((s) => (
                <ScheduledRow
                  key={s.id}
                  s={s}
                  deviceId={device.id}
                  mobile={mobile}
                  cards={cards}
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
          <DeviceSettings device={device} cards={cards} mobile={mobile} />
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
          mode={sessions.find((s) => s.name === previewName)?.mode}
          sessionId={sessions.find((s) => s.name === previewName)?.session_id}
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
