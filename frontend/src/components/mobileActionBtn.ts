// mobileActionBtn.ts — shared action button style for AllSessions / AllScheduled.
import type { CSSProperties } from 'react';
import { RT } from '../tokens';

export function mobileActionBtn(): CSSProperties {
  return {
    background: RT.panel, color: RT.text,
    border: `1px solid ${RT.border}`, borderRadius: 7,
    padding: '8px 12px', cursor: 'pointer',
    fontFamily: 'inherit', fontSize: 12, fontWeight: 500,
    display: 'inline-flex', alignItems: 'center', gap: 6, minHeight: 36,
  };
}
