// V5IconButton.tsx — 34×34 icon button with hover bg + optional accent color.
import { useState, type ReactElement, cloneElement } from 'react';
import { RT } from '../tokens';

interface Props {
  label: string;
  accent?: string;
  disabled?: boolean;
  pending?: boolean;
  onClick?: (e: React.MouseEvent) => void;
  children: ReactElement;
}

export function V5IconButton({ label, accent, disabled, pending, onClick, children }: Props) {
  const [hover, setHover] = useState(false);
  const color = accent || RT.textDim;
  return (
    <button
      title={label}
      disabled={disabled || pending}
      onClick={onClick}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        background: hover && !disabled ? RT.cardHi : RT.panel,
        color,
        border: `1px solid ${hover && accent && !disabled ? accent : RT.border}`,
        borderRadius: 7,
        width: 34,
        height: 34,
        padding: 0,
        cursor: disabled || pending ? 'default' : 'pointer',
        display: 'inline-flex',
        alignItems: 'center',
        justifyContent: 'center',
        opacity: disabled || pending ? 0.5 : 1,
        transition: 'background .12s, border-color .12s',
        flex: 'none',
      }}
    >
      {cloneElement(children, accent ? { stroke: accent } as Record<string, unknown> : {})}
    </button>
  );
}
