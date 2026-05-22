// PanelTabs.tsx — tab bar (Sessions / Scheduled / Logs).
import { RT, FONT_MONO } from '../tokens';

export type PanelTab = 'running' | 'scheduled' | 'logs';

export interface PanelTabsProps {
  tab: PanelTab;
  setTab: (t: PanelTab) => void;
  sessionCount: number;
  scheduledCount: number;
}

export function PanelTabs({ tab, setTab, sessionCount, scheduledCount }: PanelTabsProps) {
  const tabs: [PanelTab, string, number | null][] = [
    ['running',   'Sessions',  sessionCount],
    ['scheduled', 'Scheduled', scheduledCount],
    ['logs',      'Logs',      null],
  ];

  return (
    <div style={{
      flex: 'none',
      display: 'flex',
      borderBottom: `1px solid ${RT.border}`,
      padding: '0 12px',
    }}>
      {tabs.map(([id, label, count]) => (
        <button
          key={id}
          onClick={() => setTab(id)}
          style={{
            background: 'transparent',
            border: 'none',
            cursor: 'pointer',
            padding: '11px 12px',
            color: tab === id ? RT.text : RT.textLow,
            borderBottom: `2px solid ${tab === id ? RT.accent : 'transparent'}`,
            fontFamily: FONT_MONO,
            fontSize: 12,
            fontWeight: 500,
            letterSpacing: '.04em',
            textTransform: 'uppercase',
          }}
        >
          {label}
          {count !== null && (
            <span style={{ color: RT.textLow, marginLeft: 4 }}>{count}</span>
          )}
        </button>
      ))}
    </div>
  );
}
