// PanelTabs.tsx — V5 tab bar (Sessions / Scheduled / Logs / Settings) + Resume button.
import { RT, FONT_MONO } from '../tokens';
import { Icons } from './primitives';

export type PanelTab = 'running' | 'scheduled' | 'logs' | 'settings';

export interface PanelTabsProps {
  tab: PanelTab;
  setTab: (t: PanelTab) => void;
  sessionCount: number;
  scheduledCount: number;
  onResume?: () => void;
  mobile?: boolean;
}

export function PanelTabs({ tab, setTab, sessionCount, scheduledCount, onResume, mobile }: PanelTabsProps) {
  const allTabs: [PanelTab, string, number | null][] = [
    ['running',   'Sessions',  sessionCount],
    ['scheduled', 'Scheduled', scheduledCount],
    ['logs',      'Logs',      null],
    ['settings',  'Settings',  null],
  ];
  // On mobile, only show the Sessions tab — Logs/Settings are reachable via the More sheet.
  const tabs = mobile ? allTabs.slice(0, 1) : allTabs;

  return (
    <div style={{
      flex: 'none',
      display: 'flex',
      borderBottom: `1px solid ${RT.border}`,
      padding: '0 20px',
      background: RT.bg,
    }}>
      {tabs.map(([id, label, count]) => (
        <button
          key={id}
          onClick={() => setTab(id)}
          style={{
            background: 'transparent',
            border: 'none',
            cursor: 'pointer',
            padding: '13px 16px',
            color: tab === id ? RT.text : RT.textLow,
            borderBottom: `2px solid ${tab === id ? RT.text : 'transparent'}`,
            fontFamily: FONT_MONO,
            fontSize: 11,
            fontWeight: 500,
            letterSpacing: '.06em',
            textTransform: 'uppercase',
          }}
        >
          {label}
          {count !== null && (
            <span style={{ color: RT.textLow, marginLeft: 5 }}>{count}</span>
          )}
        </button>
      ))}
      <div style={{ flex: 1 }} />
      {onResume && (
        <button
          onClick={onResume}
          style={{
            background: RT.panel,
            border: `1px solid ${RT.border}`,
            borderRadius: 7,
            padding: '7px 12px',
            color: RT.text,
            fontSize: 11,
            fontWeight: 500,
            cursor: 'pointer',
            fontFamily: 'inherit',
            display: 'inline-flex',
            alignItems: 'center',
            gap: 6,
            alignSelf: 'center',
            marginBottom: 8,
          }}
        >
          <Icons.refresh size={11} stroke={RT.textDim} /> Resume
        </button>
      )}
    </div>
  );
}
