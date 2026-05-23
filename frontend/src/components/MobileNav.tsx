// MobileNav.tsx — iOS/Android-style bottom nav bar (V5MobileNav port).
import { RT, FONT_MONO } from '../tokens';
import { Icons } from './primitives';

export type MTab = 'devices' | 'sessions' | 'scheduled' | 'activity';

interface MobileNavProps {
  active: MTab;
  onChange: (tab: MTab) => void;
  counts: { devices: number; sessions: number; scheduled: number };
}

interface NavItem {
  id: MTab;
  label: string;
  icon: (p: { size?: number; stroke?: string; sw?: number }) => React.ReactNode;
  count?: number;
  dot?: boolean;
}

export function MobileNav({ active, onChange, counts }: MobileNavProps) {
  const items: NavItem[] = [
    { id: 'devices',   label: 'Devices',   icon: Icons.server,   count: counts.devices },
    { id: 'sessions',  label: 'Sessions',  icon: Icons.terminal, count: counts.sessions, dot: true },
    { id: 'scheduled', label: 'Scheduled', icon: Icons.clock,    count: counts.scheduled },
    { id: 'activity',  label: 'Activity',  icon: Icons.share },
  ];

  return (
    <div style={{
      flex: 'none',
      borderTop: `1px solid ${RT.border}`,
      background: RT.bgRaised,
      paddingBottom: 'max(8px, env(safe-area-inset-bottom))',
      paddingTop: 6,
      display: 'grid',
      gridTemplateColumns: `repeat(${items.length}, 1fr)`,
    }}>
      {items.map((it) => {
        const isActive = active === it.id;
        const color = isActive ? RT.text : RT.textLow;
        const Icon = it.icon;
        return (
          <button
            key={it.id}
            onClick={() => onChange(it.id)}
            style={{
              background: 'transparent', border: 'none', cursor: 'pointer',
              padding: '6px 4px 4px', display: 'flex', flexDirection: 'column',
              alignItems: 'center', gap: 3, color,
              fontFamily: 'inherit', minHeight: 52, position: 'relative',
            }}
          >
            <div style={{ position: 'relative', display: 'flex' }}>
              <Icon size={20} stroke={color} sw={isActive ? 2 : 1.6} />
              {it.dot && (it.count ?? 0) > 0 && (
                <span style={{
                  position: 'absolute', top: -3, right: -6,
                  minWidth: 14, height: 14, padding: '0 4px',
                  borderRadius: 7, background: RT.green, color: RT.bg,
                  fontFamily: FONT_MONO, fontSize: 9, fontWeight: 700,
                  display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
                  border: `1.5px solid ${RT.bgRaised}`,
                }}>{it.count}</span>
              )}
            </div>
            <div style={{
              fontSize: 10, fontWeight: isActive ? 600 : 500,
              letterSpacing: '.01em',
            }}>{it.label}</div>
            {isActive && (
              <div style={{
                position: 'absolute', top: 0, left: '50%', transform: 'translateX(-50%)',
                width: 24, height: 2, background: RT.text, borderRadius: 2,
              }} />
            )}
          </button>
        );
      })}
    </div>
  );
}
