import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { revalidatePath } from "next/cache";
import { getAppContext } from "@/lib/app-context";
import { adminMetrics, grantAccess, listFlags, recentAudit, setFlag } from "@/lib/repos/admin";
import { integrationSummary } from "@/lib/env";
import { recentRateLimitAlerts } from "@/lib/ratelimit";
import { getPublicPrice } from "@/lib/billing";
import { Card, PageTitle } from "@/components/app/card";
import { ProviderHealthPanel } from "@/components/app/provider-health";
import { formatMoney, relativeTime } from "@/lib/format";
import { getSessionUser } from "@/lib/auth";
import { isAdmin } from "@/lib/repos/admin";

export const metadata: Metadata = { title: "Admin" };

async function requireAdminAction() {
  const u = await getSessionUser();
  if (!u || !(await isAdmin(u.id))) throw new Error("Forbidden");
  return u;
}

async function toggleFlag(form: FormData) {
  "use server";
  const u = await requireAdminAction();
  await setFlag(u.id, String(form.get("key")), form.get("enabled") === "true");
  revalidatePath("/admin");
}

async function grant(form: FormData) {
  "use server";
  const u = await requireAdminAction();
  const days = Math.min(90, Math.max(1, Number(form.get("days") ?? 7)));
  const reason = String(form.get("reason") ?? "").trim();
  if (reason.length < 3) throw new Error("Reason required");
  await grantAccess(u.id, String(form.get("email") ?? ""), days, reason);
  revalidatePath("/admin");
}

export default async function AdminPage() {
  const ctx = await getAppContext();
  if (!ctx.isAdmin) notFound();
  const [m, flags, audit, price] = await Promise.all([adminMetrics(), listFlags(), recentAudit(15), getPublicPrice()]);
  const integ = integrationSummary();
  const alerts = recentRateLimitAlerts();
  const stat = (label: string, v: number | string) => (
    <div className="glass p-4">
      <p className="text-sm text-muted">{label}</p>
      <p className="mt-1 text-3xl font-bold">{v}</p>
    </div>
  );
  return (
    <>
      <PageTitle title="Admin console" sub="Aggregate metrics only — no payment details or locations are shown here." />
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {stat("Users", m.users)}
        {stat("New users (7d)", m.newUsers7d)}
        {stat("Active trials", m.activeTrials)}
        {stat("Active subscriptions", m.activeSubscriptions)}
        {stat("Paired devices", m.pairedDevices)}
        {stat("Send-to-Car (7d)", m.inbox7d)}
        {stat("Webhooks (24h)", m.webhookEvents24h)}
        {stat("Webhook issues (24h)", m.webhookIssues24h)}
      </div>
      <div className="mt-5 grid gap-5 xl:grid-cols-2">
        <Card title="Integrations">
          <dl className="grid grid-cols-2 gap-2 text-sm">
            {Object.entries(integ).map(([k, v]) => (
              <div key={k} className="contents">
                <dt className="text-muted">{k}</dt>
                <dd className={v === "demo" || v === "pglite" || v === "fixture" || v === "openfreemap" ? "text-warn" : v === "misconfigured" ? "text-bad" : "text-ok"}>{String(v)}</dd>
              </div>
            ))}
            <dt className="text-muted">public price</dt>
            <dd>{formatMoney(price.amountMinor, price.currency)} / {price.interval} ({price.source}) · trial {price.trialDays}d</dd>
          </dl>
          <div className="mt-4"><ProviderHealthPanel /></div>
        </Card>
        <Card title="Subscriptions by status">
          <ul className="grid gap-1 text-sm">
            {m.statuses.length === 0 && <li className="text-muted">None yet.</li>}
            {m.statuses.map((s) => (
              <li key={s.status} className="flex justify-between"><span className="text-muted">{s.status}</span><b>{s.n}</b></li>
            ))}
          </ul>
          <h3 className="mt-5 font-semibold">Provider usage today</h3>
          <table className="mt-2 w-full text-sm">
            <thead><tr className="text-left text-muted"><th>Provider</th><th>Req</th><th>Cache</th><th>Err</th><th>Quota</th></tr></thead>
            <tbody>
              {m.usage.map((u) => (
                <tr key={u.provider} className={u.overQuotaPct && u.overQuotaPct >= 80 ? "text-warn" : ""}>
                  <td>{u.provider}</td><td>{u.requests}</td><td>{u.cache_hits}</td><td>{u.errors}</td>
                  <td>{u.quota ? `${u.overQuotaPct}% of ${u.quota}` : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {m.usage.length === 0 && <p className="text-sm text-muted">No provider calls today.</p>}
        </Card>
        <Card title="Feature flags">
          <ul className="grid gap-2">
            {flags.map((f) => (
              <li key={f.key} className="flex items-center gap-3">
                <div className="flex-1">
                  <p className="font-mono text-sm">{f.key}</p>
                  <p className="text-xs text-muted">{f.description}</p>
                </div>
                <form action={toggleFlag}>
                  <input type="hidden" name="key" value={f.key} />
                  <input type="hidden" name="enabled" value={String(!f.enabled)} />
                  <button className={`btn !min-h-10 ${f.enabled ? "btn-primary" : "btn-ghost"}`} aria-pressed={f.enabled}>{f.enabled ? "On" : "Off"}</button>
                </form>
              </li>
            ))}
          </ul>
        </Card>
        <Card title="Grant temporary access (audited)">
          <form action={grant} className="grid gap-3">
            <input name="email" type="email" required placeholder="user@example.com" className="field" aria-label="User email" />
            <div className="grid grid-cols-[120px_1fr] gap-3">
              <input name="days" type="number" min={1} max={90} defaultValue={7} className="field" aria-label="Days" />
              <input name="reason" required minLength={3} maxLength={300} placeholder="Reason (e.g. support goodwill)" className="field" aria-label="Reason" />
            </div>
            <button className="btn btn-ghost justify-self-start">Grant access</button>
          </form>
        </Card>
        <Card title="Rate-limit alerts">
          {alerts.length === 0 ? <p className="text-sm text-muted">None on this instance.</p> : (
            <ul className="grid gap-1 text-sm">{alerts.slice(0, 10).map((a, i) => <li key={i} className="flex justify-between"><span className="font-mono">{a.key}</span><span className="text-muted">{relativeTime(a.at)}</span></li>)}</ul>
          )}
        </Card>
        <Card title="Audit log">
          <ul className="grid gap-1 text-sm">
            {audit.length === 0 && <li className="text-muted">No admin actions yet.</li>}
            {audit.map((a) => (
              <li key={a.id} className="flex justify-between gap-3"><span className="font-mono">{a.action}</span><span className="truncate text-muted">{JSON.stringify(a.details)}</span><span className="shrink-0 text-muted">{relativeTime(a.created_at)}</span></li>
            ))}
          </ul>
        </Card>
      </div>
    </>
  );
}
