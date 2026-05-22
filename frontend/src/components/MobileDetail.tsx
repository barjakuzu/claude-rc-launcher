// MobileDetail.tsx — RMobileDetail ported from variant-ops-refined.jsx lines 412-440.
// Full-screen overlay for mobile. Reuses PanelContent from SidePanel.
import { RT, FONT_MONO, hueForId } from '../tokens';
import { Icons, Dot } from './primitives';
import { btn } from './btn';
import { PanelContent } from './SidePanel';
import type { PanelTab } from './PanelTabs';
import type { DeviceCard } from '../types';

export interface MobileDetailProps {
  device: DeviceCard;
  tab: PanelTab;
  setTab: (t: PanelTab) => void;
  onClose: () => void;
}

export function MobileDetail({ device, tab, setTab, onClose }: MobileDetailProps) {
  const hue = hueForId(device.id);
  void hue; // hue available for future use

  return (
    <div style={{
      position: 'absolute',
      inset: 0,
      background: RT.bg,
      display: 'flex',
      flexDirection: 'column',
      zIndex: 5,
    }}>
      {/* Mobile header — back button + name + more */}
      <div style={{
        flex: 'none',
        padding: '12px 14px',
        borderBottom: `1px solid ${RT.border}`,
        background: RT.bgRaised,
        display: 'flex',
        alignItems: 'center',
        gap: 10,
      }}>
        <button onClick={onClose} style={{ ...btn('icon'), width: 30, height: 30 }}>
          <Icons.back size={14} stroke={RT.textDim} />
        </button>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <Dot color={device.online ? RT.green : RT.textLow} size={7} pulse={device.online} />
            <div style={{ fontSize: 14, fontWeight: 600 }}>{device.name}</div>
          </div>
          <div style={{ fontSize: 11, color: RT.textLow, fontFamily: FONT_MONO, marginTop: 1 }}>
            {device.hostname}
          </div>
        </div>
        {/* More button — stub, wired in Task 12 */}
        <button style={btn('icon')} onClick={() => {/* Task 12 */}}>
          <Icons.more size={14} stroke={RT.textDim} />
        </button>
      </div>

      <PanelContent device={device} tab={tab} setTab={setTab} mobile />
    </div>
  );
}
