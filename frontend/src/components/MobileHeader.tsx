// MobileHeader.tsx — cross-device page header (V5MobileHeader port).
import type { ReactNode } from 'react';
import { RT, FONT_MONO } from '../tokens';

interface MobileHeaderProps {
  subtitle: string;
  title: string;
  right?: ReactNode;
}

export function MobileHeader({ subtitle, title, right }: MobileHeaderProps) {
  return (
    <div style={{
      flex: 'none', padding: '16px 16px 12px',
      borderBottom: `1px solid ${RT.border}`, background: RT.bg,
      display: 'flex', alignItems: 'flex-end', gap: 12,
    }}>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{
          fontSize: 9.5, color: RT.textLow, letterSpacing: '.14em',
          textTransform: 'uppercase', fontFamily: FONT_MONO, marginBottom: 4,
        }}>{subtitle}</div>
        <div style={{ fontSize: 22, fontWeight: 600, letterSpacing: '-.02em' }}>{title}</div>
      </div>
      {right}
    </div>
  );
}
