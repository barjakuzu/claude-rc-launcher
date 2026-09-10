// Portal.tsx — renders children into document.body.
//
// Every overlay in this app (modal, sheet, picker, row-action menu) uses
// position:fixed and a Z.* token from tokens.ts, which is correct in
// isolation but only means what it says once the element's nearest
// stacking-context-establishing ancestor is the document root. Mounted
// anywhere deeper (e.g. inside a panel that scrolls), the overlay's
// z-index is resolved against that ancestor's stacking context instead,
// and a fixed header/nav sitting outside that subtree can paint on top of
// it regardless of the z-index value. Portaling to document.body sidesteps
// this: every overlay competes in the same root stacking context, so the Z
// scale is finally comparing like with like.
//
// It also breaks scroll chaining to whatever scrollable container the
// overlay used to be nested in: once its DOM parent is document.body
// (pinned via index.html's `body { position: fixed; inset: 0; overflow:
// hidden }`), a wheel/touch scroll that starts on the overlay's own
// backdrop has nowhere to chain to, instead of bubbling up into the
// background panel's overflow:auto column and scrolling it behind the
// overlay.
import { createPortal } from 'react-dom';
import type { ReactNode } from 'react';

export function Portal({ children }: { children: ReactNode }) {
  return createPortal(children, document.body);
}
