// Activity.tsx — timeline of schedule run history events (V5Activity port).
import { RT, FONT_MONO, tintFor, hueForId } from '../tokens';
import { Icons } from './primitives';
import { MobileHeader } from './MobileHeader';
import { useAllSchedules } from '../useCrossDevice';
import type { DeviceCard } from '../types';

interface ActivityProps {
  cards: DeviceCard[];
}

interface ActivityEvent {
  timestamp: string;
  deviceName: string;
  deviceId: string;
  scheduleName: string;
  status: string;
  message?: string;
}

function relativeTime(isoStr: string): string {
  const diff = Date.now() - new Date(isoStr).getTime();
  if (isNaN(diff)) return isoStr;
  const mins = Math.floor(diff / 60_000);
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  if (days === 1) return 'yesterday';
  // For older items, show a short date.
  try {
    return new Date(isoStr).toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
  } catch {
    return isoStr;
  }
}

function statusColor(status: string): string {
  const s = status.toLowerCase();
  if (s === 'success' || s === 'ok' || s === 'completed') return RT.green;
  if (s === 'error' || s === 'failed') return RT.red;
  return RT.amber;
}

export function Activity({ cards }: ActivityProps) {
  const schedItems = useAllSchedules(cards, true);

  // Derive events from schedule history entries.
  const events: ActivityEvent[] = [];
  for (const { device: d, schedule: s } of schedItems) {
    const hist = s.history ?? [];
    // Take latest 5 per schedule.
    for (const h of hist.slice(0, 5)) {
      events.push({
        timestamp: h.timestamp,
        deviceName: d.name,
        deviceId: d.id,
        scheduleName: s.name,
        status: h.status,
        message: h.message,
      });
    }
  }

  // Sort DESC by timestamp, take top 30.
  events.sort((a, b) => new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime());
  const visible = events.slice(0, 30);

  return (
    <div style={{ flex: 1, overflow: 'auto', display: 'flex', flexDirection: 'column' }}>
      <MobileHeader
        subtitle="last 24 hours"
        title="Activity"
        right={
          <button style={{ background: RT.panel, border: `1px solid ${RT.border}`, borderRadius: 7, width: 34, height: 34, display: 'flex', alignItems: 'center', justifyContent: 'center', cursor: 'pointer' }}>
            <Icons.filter size={14} stroke={RT.textDim} />
          </button>
        }
      />
      <div style={{ padding: '14px 16px', display: 'flex', flexDirection: 'column', gap: 0 }}>
        {visible.length === 0 && (
          <div style={{ padding: 32, textAlign: 'center', color: RT.textLow, fontFamily: FONT_MONO, fontSize: 13, border: `1px dashed ${RT.border}`, borderRadius: 10 }}>
            No recent activity.
          </div>
        )}
        {visible.map((e, i) => {
          const hue = hueForId(e.deviceId);
          const hueColor = tintFor(hue, 0.70, 0.10);
          const kindColor = statusColor(e.status);
          return (
            <div key={i} style={{ display: 'flex', gap: 12, position: 'relative', paddingBottom: i === visible.length - 1 ? 0 : 14 }}>
              {/* Timeline dot + line */}
              <div style={{ flex: 'none', width: 14, display: 'flex', flexDirection: 'column', alignItems: 'center' }}>
                <span style={{
                  width: 8, height: 8, borderRadius: 4, background: hueColor,
                  marginTop: 5, border: `2px solid ${RT.bg}`,
                  boxShadow: `0 0 0 1.5px ${hueColor}`,
                }} />
                {i < visible.length - 1 && (
                  <span style={{ flex: 1, width: 1, background: RT.border, marginTop: 4 }} />
                )}
              </div>
              {/* Content */}
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: 13, color: RT.text, lineHeight: 1.4 }}>
                  {e.message ?? `${e.scheduleName} ran`}
                </div>
                <div style={{ marginTop: 4, fontSize: 10.5, fontFamily: FONT_MONO, color: RT.textLow, display: 'flex', alignItems: 'center', gap: 6 }}>
                  <span style={{ color: hueColor }}>{e.deviceName}</span>
                  <span style={{ color: RT.borderHi }}>·</span>
                  <span>{relativeTime(e.timestamp)}</span>
                  <span style={{ color: RT.borderHi }}>·</span>
                  <span style={{ color: kindColor, letterSpacing: '.06em', textTransform: 'uppercase' }}>{e.status}</span>
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
