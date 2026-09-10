// menuPos.ts — fixed-position popover placement that escapes overflow clipping.
// Row menus used to open with position:absolute bottom:100%, which the
// scrollable panel body clipped at its top edge (only the last item survived).
// position:fixed is laid out against the viewport, so nothing clips it; we
// flip direction based on the space below the anchor.
import { useEffect } from 'react';
import type { CSSProperties } from 'react';

export function fixedMenuPos(
  anchor: HTMLElement,
  opts?: { menuH?: number; align?: 'left' | 'right' },
): CSSProperties {
  const r = anchor.getBoundingClientRect();
  const menuH = opts?.menuH ?? 190;
  const pos: CSSProperties = { position: 'fixed' };
  if ((opts?.align ?? 'right') === 'right') {
    pos.right = Math.max(8, window.innerWidth - r.right);
  } else {
    pos.left = Math.max(8, r.left);
  }
  if (window.innerHeight - r.bottom >= menuH + 8) {
    pos.top = r.bottom + 4; // open downward
  } else {
    pos.bottom = Math.max(8, window.innerHeight - r.top + 4); // open upward
  }
  return pos;
}

/** Closes an open row-action menu on scroll or resize.
 *
 * fixedMenuPos above computes a position once, at open time, from the
 * trigger's getBoundingClientRect -- it does not track the trigger
 * continuously. Now that the menu portals to document.body (see
 * Portal.tsx), it also no longer scrolls together with the list it was
 * opened from: once that list (or any other ancestor) scrolls, the
 * anchor has moved and the menu would be left floating next to nothing.
 * Closing on scroll is simpler and less surprising than re-measuring and
 * repositioning on every scroll frame.
 *
 * The scroll listener is registered on window with capture:true rather
 * than on any specific container: scroll events don't bubble, but a
 * capturing listener on window still observes them on the way down to
 * the actual scrolling element, whichever nested container that turns
 * out to be. */
export function useCloseMenuOnScroll(open: boolean, onClose: () => void): void {
  useEffect(() => {
    if (!open) return;
    window.addEventListener('scroll', onClose, true);
    window.addEventListener('resize', onClose);
    return () => {
      window.removeEventListener('scroll', onClose, true);
      window.removeEventListener('resize', onClose);
    };
  }, [open, onClose]);
}
