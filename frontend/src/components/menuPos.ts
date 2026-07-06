// menuPos.ts — fixed-position popover placement that escapes overflow clipping.
// Row menus used to open with position:absolute bottom:100%, which the
// scrollable panel body clipped at its top edge (only the last item survived).
// position:fixed is laid out against the viewport, so nothing clips it; we
// flip direction based on the space below the anchor.
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
