export function Card({ title, action, children, className = "" }: { title?: React.ReactNode; action?: React.ReactNode; children: React.ReactNode; className?: string }) {
  return (
    <section className={`glass p-5 ${className}`}>
      {(title || action) && (
        <div className="mb-4 flex items-center justify-between gap-3">
          {title && <h2 className="text-lg font-semibold">{title}</h2>}
          {action}
        </div>
      )}
      {children}
    </section>
  );
}

export function PageTitle({ title, sub, action }: { title: string; sub?: string; action?: React.ReactNode }) {
  return (
    <div className="mb-5 flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 className="text-3xl font-bold tracking-tight">{title}</h1>
        {sub && <p className="mt-1 text-muted">{sub}</p>}
      </div>
      {action}
    </div>
  );
}

export function StatusPill({ status, label }: { status: string; label: string }) {
  const tone =
    status === "active" || status === "trialing"
      ? "border-ok/40 bg-ok/15 text-ok"
      : status === "past_due" || status === "scheduled_cancel"
        ? "border-warn/40 bg-warn/15 text-warn"
        : "border-line bg-white/5 text-muted";
  return <span className={`chip ${tone}`}>{label}</span>;
}
