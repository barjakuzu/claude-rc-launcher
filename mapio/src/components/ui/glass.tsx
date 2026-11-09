/**
 * MAPIO design system primitives (server-safe; no hooks). Interactive pieces live in
 * ./overlay.tsx. All styling flows from the tokens in app/globals.css.
 */
import Link from "next/link";
import type { ComponentProps, ReactNode } from "react";

type Tier = "surface" | "elevated" | "solid";
const tierClass: Record<Tier, string> = {
  surface: "glass",
  elevated: "glass-elevated",
  solid: "glass-strong",
};

export function GlassPanel({
  tier = "surface",
  accent = false,
  className = "",
  as: Tag = "div",
  ...rest
}: { tier?: Tier; accent?: boolean; as?: "div" | "section" | "aside" | "nav" | "article" | "header" } & ComponentProps<"div">) {
  return <Tag className={`${tierClass[tier]} ${accent ? "glass-accent" : ""} ${className}`} {...(rest as object)} />;
}

/** Card with an optional title row. Use for grouped content, not for every block. */
export function GlassCard({
  title,
  eyebrow,
  action,
  children,
  className = "",
  tier = "surface",
  accent,
  padded = true,
}: {
  title?: ReactNode;
  eyebrow?: string;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
  tier?: Tier;
  accent?: boolean;
  padded?: boolean;
}) {
  return (
    <GlassPanel as="section" tier={tier} accent={accent} className={`${padded ? "p-5 sm:p-6" : ""} ${className}`}>
      {(title || action || eyebrow) && (
        <div className="mb-4 flex items-start justify-between gap-3">
          <div className="min-w-0">
            {eyebrow && <p className="eyebrow mb-1">{eyebrow}</p>}
            {title && <h2 className="text-[17px] font-semibold tracking-tight">{title}</h2>}
          </div>
          {action}
        </div>
      )}
      {children}
    </GlassPanel>
  );
}

type BtnVariant = "primary" | "ghost" | "danger";
type BtnSize = "sm" | "md" | "lg";
const sizeClass: Record<BtnSize, string> = {
  sm: "!min-h-10 !px-4 text-sm",
  md: "",
  lg: "!min-h-14 !px-7 text-lg",
};

export function GlassButton({
  variant = "primary",
  size = "md",
  className = "",
  ...rest
}: { variant?: BtnVariant; size?: BtnSize } & ComponentProps<"button">) {
  return <button className={`btn btn-${variant} ${sizeClass[size]} ${className}`} {...rest} />;
}

export function GlassLink({
  variant = "primary",
  size = "md",
  className = "",
  ...rest
}: { variant?: BtnVariant; size?: BtnSize } & ComponentProps<typeof Link>) {
  return <Link className={`btn btn-${variant} ${sizeClass[size]} ${className}`} {...rest} />;
}

export function GlassInput({ label, className = "", ...rest }: { label?: string } & ComponentProps<"input">) {
  const input = <input className={`field text-base ${className}`} {...rest} />;
  if (!label) return input;
  return (
    <label className="grid gap-1.5 text-sm font-medium">
      {label}
      {input}
    </label>
  );
}

/** Square control used over maps (toolbar) and in headers. */
export function MapControlButton({ className = "", pressed, ...rest }: { pressed?: boolean } & ComponentProps<"button">) {
  return <button type="button" className={`ctl ${className}`} aria-pressed={pressed} {...rest} />;
}

/** Compact status pill: weather, FX, connection, plan state. */
export function StatusWidget({
  icon,
  children,
  tone = "neutral",
  className = "",
  title,
}: {
  icon?: ReactNode;
  children: ReactNode;
  tone?: "neutral" | "ok" | "warn" | "bad" | "accent";
  className?: string;
  title?: string;
}) {
  const toneClass = {
    neutral: "",
    ok: "!border-ok/40 text-ok",
    warn: "!border-warn/40 text-warn",
    bad: "!border-bad/40 text-bad",
    accent: "!border-accent-2/40 text-accent-2",
  }[tone];
  return (
    <span className={`chip ${toneClass} ${className}`} title={title}>
      {icon}
      {children}
    </span>
  );
}

/** Decorative glossy icon tile (feature icons, plan mark). */
export function IconTile({ children, tint = "from-[#5ad8ff] to-[#2f6bff]", size = "md" }: { children: ReactNode; tint?: string; size?: "sm" | "md" | "lg" }) {
  const dim = { sm: "h-10 w-10 rounded-xl", md: "h-14 w-14 rounded-2xl", lg: "h-16 w-16 rounded-[1.25rem]" }[size];
  return (
    <span className={`relative grid ${dim} shrink-0 place-items-center overflow-hidden bg-gradient-to-br ${tint} text-white shadow-[0_12px_28px_-10px_var(--glow)]`}>
      <span aria-hidden className="absolute inset-x-0 top-0 h-1/2 bg-gradient-to-b from-white/35 to-transparent" />
      <span className="relative">{children}</span>
    </span>
  );
}

/**
 * Cinematic backdrop for marketing/auth: aurora ribbons, layered mountain silhouettes and
 * road light streaks. Pure CSS/SVG, fixed behind content, no images. Static (no infinite
 * animation) so it never competes with the map for GPU time.
 */
export function AmbientBackground({ intensity = "full" }: { intensity?: "full" | "soft" }) {
  return (
    <div aria-hidden data-allow-overflow className="pointer-events-none fixed inset-0 z-0 overflow-hidden bg-[linear-gradient(180deg,#030814_0%,#071430_55%,#081a38_100%)]">
      <div className="absolute -left-[10%] -top-[18%] h-[70vh] w-[70vw] rounded-full bg-[radial-gradient(closest-side,rgba(38,230,200,0.28),transparent)] blur-3xl" />
      <div className="absolute left-[18%] -top-[10%] h-[48vh] w-[48vw] rotate-[-12deg] rounded-full bg-[radial-gradient(closest-side,rgba(40,170,255,0.24),transparent)] blur-3xl" />
      <div className="absolute -right-[12%] -top-[12%] h-[60vh] w-[55vw] rounded-full bg-[radial-gradient(closest-side,rgba(60,110,255,0.26),transparent)] blur-3xl" />
      {intensity === "full" && (
        <>
          {/* aurora ribbon */}
          <div className="absolute left-[-5%] top-[6%] h-[22vh] w-[70vw] rotate-[-8deg] rounded-[100%] bg-[linear-gradient(90deg,transparent,rgba(60,240,210,0.22)_30%,rgba(60,200,255,0.18)_60%,transparent)] blur-2xl" />
          {/* mountains */}
          <svg className="absolute inset-x-0 bottom-[18vh] h-[34vh] w-full opacity-70" viewBox="0 0 1440 320" preserveAspectRatio="none">
            <path d="M0 250 L120 170 L210 215 L330 110 L430 190 L560 120 L690 205 L820 90 L940 180 L1060 130 L1190 200 L1320 140 L1440 190 L1440 320 L0 320 Z" fill="#0b1b38" />
            <path d="M0 285 L150 230 L280 262 L420 205 L560 255 L720 200 L880 252 L1030 215 L1180 262 L1320 230 L1440 255 L1440 320 L0 320 Z" fill="#081530" />
          </svg>
          {/* ground + road light streaks */}
          <div className="absolute inset-x-0 bottom-0 h-[24vh] bg-[linear-gradient(180deg,#081530,#050d20)]" />
          <div className="absolute bottom-[10vh] left-[-10%] h-[3px] w-[70vw] rotate-[-4deg] bg-[linear-gradient(90deg,transparent,rgba(80,170,255,0.45),transparent)] blur-[1px]" />
          <div className="absolute bottom-[7vh] right-[-10%] h-[2px] w-[60vw] rotate-[3deg] bg-[linear-gradient(90deg,transparent,rgba(255,90,90,0.3),transparent)] blur-[1px]" />
          <div className="absolute bottom-[4vh] left-[15%] h-[2px] w-[55vw] bg-[linear-gradient(90deg,transparent,rgba(120,210,255,0.4),transparent)] blur-[2px]" />
        </>
      )}
    </div>
  );
}
