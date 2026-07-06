// MobileMoreSheet.tsx — bottom sheet opened by the "More" nav button.
import { useEffect } from 'react';
import { RT, FONT_MONO, Z } from '../tokens';
import { Icons } from './primitives';
import type { MTab } from './MobileNav';
import type { PanelTab } from './PanelTabs';
import type { DeviceCard } from '../types';

interface MobileMoreSheetProps {
  open: boolean;
  onClose: () => void;
  openId: string | null;
  cards: DeviceCard[];
  setOpenId: (id: string | null) => void;
  setMTab: (t: MTab) => void;
  setDeviceTab: (t: PanelTab) => void;
  onShareOpen: () => void;
}

interface SheetItem {
  icon: (p: { size?: number; stroke?: string; sw?: number }) => React.ReactNode;
  label: string;
  subtitle?: string;
  disabled?: boolean;
  danger?: boolean;
  onClick: () => void;
}

export function MobileMoreSheet({
  open,
  onClose,
  openId,
  setMTab,
  setDeviceTab,
  onShareOpen,
}: MobileMoreSheetProps) {
  // Close on Escape key.
  useEffect(() => {
    if (!open) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, [open, onClose]);

  const deviceOpen = openId !== null;

  const items: SheetItem[] = [
    {
      icon: Icons.clock,
      label: 'Activity',
      onClick: () => { setMTab('activity'); onClose(); },
    },
    {
      icon: Icons.terminal,
      label: 'Logs',
      subtitle: deviceOpen ? undefined : 'Open a device first',
      disabled: !deviceOpen,
      onClick: () => {
        if (!deviceOpen) return;
        setMTab('devices');
        setDeviceTab('logs');
        onClose();
      },
    },
    {
      icon: Icons.filter,
      label: 'Settings',
      subtitle: deviceOpen ? undefined : 'Open a device first',
      disabled: !deviceOpen,
      onClick: () => {
        if (!deviceOpen) return;
        setMTab('devices');
        setDeviceTab('settings');
        onClose();
      },
    },
  ];

  const dividerStyle: React.CSSProperties = {
    height: 1,
    background: RT.border,
    margin: '4px 0',
  };

  const secondaryItems: SheetItem[] = [
    {
      icon: Icons.share,
      label: 'Share tunnel',
      onClick: () => { onShareOpen(); onClose(); },
    },
    {
      icon: Icons.back,
      label: 'Classic UI',
      onClick: () => { window.location.href = '/legacy'; },
    },
    {
      icon: Icons.power,
      label: 'Log out',
      danger: true,
      onClick: () => { window.location.href = '/logout'; },
    },
  ];

  return (
    <>
      {/* Backdrop */}
      {open && (
        <div
          onClick={onClose}
          style={{
            position: 'fixed',
            inset: 0,
            background: 'rgba(0,0,0,.5)',
            zIndex: Z.sheet,
          }}
        />
      )}

      {/* Sheet */}
      <div
        style={{
          position: 'fixed',
          left: 0,
          right: 0,
          bottom: 0,
          zIndex: Z.sheet + 1,
          background: RT.panel,
          border: `1px solid ${RT.borderHi}`,
          borderTopLeftRadius: 16,
          borderTopRightRadius: 16,
          padding: 12,
          // Reserve space for the bottom nav above which this sheet sits.
          // The nav is ~60px; we push the sheet content up but allow it to
          // extend to the bottom edge so the backdrop fills behind the nav too.
          transform: open ? 'translateY(0)' : 'translateY(100%)',
          transition: 'transform 240ms cubic-bezier(.2,.7,.2,1)',
          // drag handle
          paddingTop: 16,
        }}
      >
        {/* Drag handle */}
        <div style={{
          width: 36, height: 4, borderRadius: 2,
          background: RT.borderHi, margin: '0 auto 12px',
        }} />

        {/* Primary items */}
        {items.map((item) => (
          <SheetRow key={item.label} item={item} />
        ))}

        <div style={dividerStyle} />

        {/* Secondary items */}
        {secondaryItems.map((item) => (
          <SheetRow key={item.label} item={item} />
        ))}
      </div>
    </>
  );
}

function SheetRow({ item }: { item: { icon: (p: { size?: number; stroke?: string; sw?: number }) => React.ReactNode; label: string; subtitle?: string; disabled?: boolean; danger?: boolean; onClick: () => void } }) {
  const Icon = item.icon;
  const color = item.danger ? RT.red : item.disabled ? RT.textLow : RT.text;
  const subtitleColor = RT.textLow;

  return (
    <button
      onClick={item.onClick}
      disabled={item.disabled}
      style={{
        width: '100%',
        minHeight: 56,
        display: 'flex',
        alignItems: 'center',
        gap: 14,
        padding: '10px 12px',
        background: 'transparent',
        border: 'none',
        borderRadius: 10,
        cursor: item.disabled ? 'default' : 'pointer',
        color,
        fontFamily: 'inherit',
        textAlign: 'left',
        opacity: item.disabled ? 0.6 : 1,
        transition: 'background 120ms',
      }}
      onMouseEnter={(e) => {
        if (!item.disabled) (e.currentTarget as HTMLButtonElement).style.background = RT.bgRaised;
      }}
      onMouseLeave={(e) => {
        (e.currentTarget as HTMLButtonElement).style.background = 'transparent';
      }}
    >
      <Icon size={20} stroke={color} sw={1.6} />
      <div style={{ flex: 1 }}>
        <div style={{ fontSize: 15, fontWeight: 500 }}>{item.label}</div>
        {item.subtitle && (
          <div style={{ fontSize: 11, color: subtitleColor, fontFamily: FONT_MONO, marginTop: 2 }}>
            {item.subtitle}
          </div>
        )}
      </div>
      {!item.disabled && (
        <Icons.chevRight size={16} stroke={RT.textLow} />
      )}
    </button>
  );
}
