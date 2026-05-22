// btn.ts — button style helper.
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
    borderRadius: 7,
    width: 32,
    height: 32,
    padding: 0,
    color: RT.textDim,
  };
  if (kind === 'mini') return {
    ...base,
    background: 'transparent',
    color: RT.textDim,
    border: `1px solid ${RT.border}`,
    borderRadius: 6,
    width: 28,
    height: 28,
    padding: 0,
  };
  if (kind === 'tinyText') return {
    ...base,
    background: RT.panel,
    border: `1px solid ${RT.border}`,
    borderRadius: 6,
    padding: '7px 12px',
    color: RT.text,
    fontSize: 13,
    fontWeight: 500,
    gap: '5px',
  } as CSSProperties;
  return base;
}
