// DevicePicker.tsx — modal overlay to pick a target device (used by Copy/Move).
import { useEffect } from 'react';
import { RT, FONT_MONO, tintFor, hueForId, kindForOs } from '../tokens';
import { Icons, Dot } from './primitives';
import type { DeviceCard } from '../types';

export interface DevicePickerProps {
  cards: DeviceCard[];
  excludeDeviceId: string;
  title: string;
  caveat?: string;
  onPick: (deviceId: string) => void;
  onClose: () => void;
}

export function DevicePicker({ cards, excludeDeviceId, title, caveat, onPick, onClose }: DevicePickerProps) {
  const targets = cards.filter((c) => c.id !== excludeDeviceId);

  // Close on Escape key.
  useEffect(() => {
    const handler = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, [onClose]);

  return (
    <div
      style={{
        position: 'fixed', inset: 0, zIndex: 100,
        background: 'rgba(0,0,0,0.55)',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
      }}
      onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div style={{
        background: RT.panel, border: `1px solid ${RT.borderHi}`,
        borderRadius: 14, padding: 18,
        boxShadow: '0 16px 48px rgba(0,0,0,.55)',
        minWidth: 280, width: 340,
        maxWidth: 'calc(100vw - 28px)',
      }}>
        {/* Title */}
        <div style={{ fontSize: 14, fontWeight: 600, marginBottom: caveat ? 8 : 14 }}>
          {title}
        </div>

        {/* Caveat warning */}
        {caveat && (
          <div style={{
            fontSize: 11, fontFamily: FONT_MONO, color: 'oklch(0.72 0.09 25)',
            background: 'oklch(0.62 0.12 25 / 0.10)',
            border: '1px solid oklch(0.62 0.12 25 / 0.25)',
            borderRadius: 6, padding: '7px 10px', marginBottom: 14, lineHeight: 1.5,
          }}>
            {caveat}
          </div>
        )}

        {targets.length === 0 ? (
          <div style={{ padding: '24px 0', textAlign: 'center', color: RT.textLow, fontFamily: FONT_MONO, fontSize: 12 }}>
            No other devices available.
          </div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {targets.map((card) => {
              const hue = hueForId(card.id);
              const chipColor = tintFor(hue, 0.70, 0.10);
              const IconFn = Icons[kindForOs(card.os)];
              const dotColor = card.online ? RT.green : RT.textLow;
              return (
                <button
                  key={card.id}
                  onClick={() => onPick(card.id)}
                  style={{
                    background: RT.card, border: `1px solid ${RT.border}`,
                    borderRadius: 8, padding: '10px 13px', cursor: 'pointer',
                    textAlign: 'left', display: 'flex', alignItems: 'center', gap: 10,
                    width: '100%', transition: 'border-color .12s, background .12s',
                  }}
                  onMouseEnter={(e) => {
                    (e.currentTarget as HTMLButtonElement).style.background = RT.cardHi;
                    (e.currentTarget as HTMLButtonElement).style.borderColor = RT.borderHi;
                  }}
                  onMouseLeave={(e) => {
                    (e.currentTarget as HTMLButtonElement).style.background = RT.card;
                    (e.currentTarget as HTMLButtonElement).style.borderColor = RT.border;
                  }}
                >
                  {/* Device icon */}
                  <span style={{ color: chipColor, flex: 'none' }}>
                    <IconFn size={15} stroke={chipColor} />
                  </span>

                  {/* Name + hostname */}
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontSize: 13, fontWeight: 600, color: RT.text, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                      {card.name}
                    </div>
                    <div style={{ fontSize: 10, fontFamily: FONT_MONO, color: RT.textLow, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                      {card.hostname}
                    </div>
                  </div>

                  {/* Online/offline dot */}
                  <Dot color={dotColor} size={6} pulse={card.online} />
                </button>
              );
            })}
          </div>
        )}

        {/* Close button (always shown, especially useful for empty state) */}
        <div style={{ marginTop: 14, display: 'flex', justifyContent: 'flex-end' }}>
          <button
            onClick={onClose}
            style={{
              background: 'transparent', border: `1px solid ${RT.border}`,
              borderRadius: 6, padding: '6px 14px', cursor: 'pointer',
              color: RT.textDim, fontSize: 12, fontFamily: 'inherit',
            }}
          >
            Cancel
          </button>
        </div>
      </div>
    </div>
  );
}
