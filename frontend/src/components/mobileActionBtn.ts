// mobileActionBtn.ts — shared action button style for AllSessions / AllScheduled.
import type { CSSProperties } from 'react';
import { RT } from '../tokens';

export function mobileActionBtn(): CSSProperties {
  return {
    background: RT.panel, color: RT.text,
    border: `1px solid ${RT.border}`, borderRadius: 7,
    padding: '10px 12px', cursor: 'pointer',
    fontFamily: 'inherit', fontSize: 12, fontWeight: 500,
    // 44px is the touch-target floor (Apple HIG / Material). This used to
    // be 36 — several of these per row is the most common way a web app
    // announces it is not native. The row this sits in must wrap
    // (flexWrap) rather than overflow now that these are taller/wider.
    display: 'inline-flex', alignItems: 'center', gap: 6, minHeight: 44,
  };
}
