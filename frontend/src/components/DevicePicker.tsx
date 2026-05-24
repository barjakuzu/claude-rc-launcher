// DevicePicker.tsx — modal overlay to pick a target device (used by Copy/Move).
import { useEffect, useState } from 'react';
import { RT, FONT_MONO, tintFor, hueForId, kindForOs } from '../tokens';
import { Icons, Dot } from './primitives';
import type { DeviceCard } from '../types';

export interface DevicePickResult {
  deviceId: string;
  /** If true, callers should rewrite source-home paths to target-home paths in the copied content. */
  rewritePaths: boolean;
}

export interface DevicePickerProps {
  cards: DeviceCard[];
  excludeDeviceId: string;
  title: string;
  caveat?: string;
  /** Optional: source device's home dir (e.g., "/root"). Used to detect whether
   *  any target has a different home and offer to rewrite paths. */
  sourceHomeDir?: string;
  /** Optional: a sample of the content being copied (workdir + prompt) used
   *  to decide whether to show the rewrite checkbox at all. */
  contentSample?: string;
  onPick: (result: DevicePickResult) => void;
  onClose: () => void;
}

export function DevicePicker({
  cards, excludeDeviceId, title, caveat,
  sourceHomeDir, contentSample,
  onPick, onClose,
}: DevicePickerProps) {
  const targets = cards.filter((c) => c.id !== excludeDeviceId);
  const [rewrite, setRewrite] = useState(true);

  // Close on Escape key.
  useEffect(() => {
    const handler = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, [onClose]);

  // The "rewrite" affordance is meaningful only when:
  //   - We know the source home dir AND
  //   - At least one target has a different, known home dir AND
  //   - The content being copied contains the source home dir as a substring.
  const contentHasSourceHome = !!(
    sourceHomeDir && contentSample && contentSample.includes(sourceHomeDir)
  );
  const anyTargetHasDifferentHome = targets.some(
    (c) => c.home_dir && sourceHomeDir && c.home_dir !== sourceHomeDir,
  );
  const showRewriteToggle = contentHasSourceHome && anyTargetHasDifferentHome;

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
        minWidth: 280, width: 380,
        maxWidth: 'calc(100vw - 28px)',
      }}>
        {/* Title */}
        <div style={{ fontSize: 14, fontWeight: 600, marginBottom: caveat || showRewriteToggle ? 8 : 14 }}>
          {title}
        </div>

        {/* Caveat warning */}
        {caveat && (
          <div style={{
            fontSize: 11, fontFamily: FONT_MONO, color: 'oklch(0.72 0.09 25)',
            background: 'oklch(0.62 0.12 25 / 0.10)',
            border: '1px solid oklch(0.62 0.12 25 / 0.25)',
            borderRadius: 6, padding: '7px 10px', marginBottom: 12, lineHeight: 1.5,
          }}>
            {caveat}
          </div>
        )}

        {/* Path rewrite toggle */}
        {showRewriteToggle && (
          <label style={{
            display: 'flex', alignItems: 'flex-start', gap: 8,
            background: RT.bgRaised, border: `1px solid ${RT.border}`,
            borderRadius: 8, padding: '8px 10px', marginBottom: 14,
            cursor: 'pointer',
          }}>
            <input
              type="checkbox"
              checked={rewrite}
              onChange={(e) => setRewrite(e.target.checked)}
              style={{ accentColor: RT.accent, marginTop: 3, flex: 'none' }}
            />
            <span style={{ fontSize: 12, color: RT.text, lineHeight: 1.45 }}>
              <span style={{ fontWeight: 600 }}>Rewrite paths</span> for target's home dir
              <div style={{ fontSize: 10.5, fontFamily: FONT_MONO, color: RT.textLow, marginTop: 3, letterSpacing: '.02em' }}>
                <span>{sourceHomeDir}/</span>
                <span style={{ color: RT.textDim }}> → </span>
                <span>{`<target-home>/`}</span>
              </div>
            </span>
          </label>
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
              const targetHome = card.home_dir;
              const homesDiffer = sourceHomeDir && targetHome && targetHome !== sourceHomeDir;
              return (
                <button
                  key={card.id}
                  onClick={() => onPick({ deviceId: card.id, rewritePaths: rewrite && !!homesDiffer })}
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

                  {/* Name + hostname + home_dir */}
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontSize: 13, fontWeight: 600, color: RT.text, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                      {card.name}
                    </div>
                    <div style={{ fontSize: 10, fontFamily: FONT_MONO, color: RT.textLow, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                      {card.hostname}
                      {targetHome && (
                        <>
                          <span style={{ color: RT.borderHi }}> · </span>
                          <span style={{ color: homesDiffer ? RT.amber : RT.textLow }}>{targetHome}</span>
                        </>
                      )}
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

/** Path-rewrite helper: replace every occurrence of `fromHome` with `toHome`.
 *  Used by callers when DevicePickResult.rewritePaths is true. */
export function rewriteHomePaths(text: string, fromHome: string, toHome: string): string {
  if (!text || !fromHome || !toHome || fromHome === toHome) return text;
  const from = fromHome.replace(/\/+$/, '');
  const to = toHome.replace(/\/+$/, '');
  return text.split(from).join(to);
}
