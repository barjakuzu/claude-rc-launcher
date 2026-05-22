// btn.ts — button style helper. Ported from variant-ops-refined.jsx lines 646-665.
import type { CSSProperties } from 'react';
import { RT } from '../tokens';

export type BtnKind = 'icon' | 'mini' | 'tinyText' | 'base';

export function btn(kind: BtnKind): CSSProperties {
  const base: React.CSSProperties = {
    fontFamily: 'inherit',
    cursor: 'pointer',
    display: 'inline-flex',
    alignItems: 'center',
    justifyContent: 'center',
  };
  if (kind === 'icon') return {
    ...base,
    background: RT.panel,
    border: `1px solid ${RT.border}`,
    borderRadius: 6,
    width: 26,
    height: 26,
    padding: 0,
    color: RT.textDim,
  };
  if (kind === 'mini') return {
    ...base,
    background: 'transparent',
    color: RT.textDim,
    border: `1px solid ${RT.border}`,
    borderRadius: 4,
    width: 22,
    height: 22,
    padding: 0,
  };
  if (kind === 'tinyText') return {
    ...base,
    background: RT.panel,
    border: `1px solid ${RT.border}`,
    borderRadius: 6,
    padding: '5px 10px',
    color: RT.text,
    fontSize: 11,
    fontWeight: 500,
    gap: '5px',
  } as CSSProperties;
  return base;
}
