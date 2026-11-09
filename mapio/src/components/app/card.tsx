import type { ReactNode } from "react";
import { GlassCard } from "@/components/ui/glass";

/** Back-compat wrapper: pages use <Card>; it renders the design-system GlassCard. */
export function Card({ title, action, children, className = "" }: { title?: ReactNode; action?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <GlassCard title={title} action={action} className={`!rounded-[24px] ${className}`}>
      {children}
    </GlassCard>
  );
}

export function PageTitle({ title, sub, action }: { title: string; sub?: string; action?: ReactNode }) {
  return (
    <div className="rise mb-5 flex flex-wrap items-end justify-between gap-3">
      <div className="min-w-0">
        <h1 className="text-[1.75rem] font-semibold leading-tight tracking-[-0.02em] sm:text-3xl">{title}</h1>
        {sub && <p className="mt-1 max-w-2xl text-[15px] text-muted">{sub}</p>}
      </div>
      {action}
    </div>
  );
}

export function StatusPill({ status, label }: { status: string; label: string }) {
  const tone =
    status === "active" || status === "trialing"
      ? "!border-ok/40 text-ok"
      : status === "past_due" || status === "scheduled_cancel"
        ? "!border-warn/40 text-warn"
        : "text-muted";
  return (
    <span className={`chip ${tone}`}>
      <span aria-hidden className={`h-1.5 w-1.5 rounded-full ${tone.includes("ok") ? "bg-ok" : tone.includes("warn") ? "bg-warn" : "bg-faint"}`} />
      {label}
    </span>
  );
}
