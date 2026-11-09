/** MAPIO wordmark — custom "M" mark (two peaks), never a third-party logo. */
export function LogoMark({ size = 32, className }: { size?: number; className?: string }) {
  return (
    <svg width={size} height={size} viewBox="0 0 48 40" aria-hidden className={className}>
      <defs>
        <linearGradient id="mapio-m" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#59e3ff" />
          <stop offset="1" stopColor="#1f6fff" />
        </linearGradient>
      </defs>
      <path
        d="M3 36 L14 6 Q15.5 2 17.5 6 L24 21 L30.5 6 Q32.5 2 34 6 L45 36 Q46 39 42.5 38.5 L37 37.5 L31.5 20 L26.5 31.5 Q24 36 21.5 31.5 L16.5 20 L11 37.5 L5.5 38.5 Q2 39 3 36 Z"
        fill="url(#mapio-m)"
      />
    </svg>
  );
}

export function Logo({ className = "" }: { className?: string }) {
  return (
    <span className={`inline-flex items-center gap-2.5 ${className}`}>
      <LogoMark />
      <span className="text-xl font-bold tracking-[0.12em] text-text">MAPIO</span>
    </span>
  );
}
