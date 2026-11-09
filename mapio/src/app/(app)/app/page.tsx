import type { Metadata } from "next";
import Link from "next/link";
import { ArrowRight, Briefcase, ChevronRight, Clock, Home, MapPin, Navigation, Search, Send, Star, Zap } from "lucide-react";
import { getAppContext, getMapPageData } from "@/lib/app-context";
import { listRecent, listSaved } from "@/lib/repos/places";
import { listAllInbox } from "@/lib/repos/inbox";
import { GlassCard, GlassPanel, IconTile } from "@/components/ui/glass";
import { StatusPill } from "@/components/app/card";
import { TopChips } from "@/components/app/top-chips";
import { MiniMap } from "@/components/map/mini-map";
import { CheckoutButton } from "@/components/billing/billing-controls";
import { LogoMark } from "@/components/ui/logo";
import { formatDate, formatMoney, relativeTime } from "@/lib/format";

export const metadata: Metadata = { title: "Dashboard" };

const mapHref = (p: { lat: number; lon: number; name: string }) =>
  `/app/map?lat=${p.lat}&lon=${p.lon}&name=${encodeURIComponent(p.name)}`;

export default async function DashboardPage() {
  const ctx = await getAppContext();
  const { t, entitlement, billing } = ctx;
  const [map, saved, recent, inbox] = await Promise.all([
    getMapPageData(ctx.user.id),
    listSaved(ctx.user.id),
    listRecent(ctx.user.id, 6),
    listAllInbox(ctx.user.id, 4),
  ]);
  const name = ctx.profile.display_name || ctx.user.email.split("@")[0];
  const sub = billing.subscription;
  const latest = recent[0];
  const locked = !entitlement.hasAccess;
  const statusLabel = t[`plan.${entitlement.status}` as keyof typeof t] ?? entitlement.status;
  const planLine =
    entitlement.status === "trialing" && sub?.trial_end
      ? `Trial ends ${formatDate(sub.trial_end)}`
      : entitlement.status === "scheduled_cancel"
        ? `Access until ${formatDate(entitlement.accessUntil)}`
        : entitlement.status === "active" && sub?.next_billed_at
          ? `Renews ${formatDate(sub.next_billed_at)}${sub.unit_price_amount != null && sub.currency_code ? ` · ${formatMoney(Number(sub.unit_price_amount), sub.currency_code)}` : ""}`
          : entitlement.status === "past_due"
            ? "Payment needs attention"
            : entitlement.hasAccess
              ? `Access until ${formatDate(entitlement.accessUntil)}`
              : billing.trialEligible
                ? `Try everything free for ${billing.trialDays} days`
                : "No active plan";

  const quick = [
    { href: "/app/saved", label: "Saved Places", icon: Star, tint: "from-[#c2b2ff] to-[#6b5cff]" },
    { href: "/app/devices", label: "Send to Car", icon: Send, tint: "from-[#8ccfff] to-[#3a7bff]" },
    { href: "/app/chargers", label: "Chargers", icon: Zap, tint: "from-[#a597ff] to-[#5a4bff]" },
    { href: "/drive", label: "Drive mode", icon: Navigation, tint: "from-[#63e0ff] to-[#2f6bff]" },
  ];

  return (
    <div className="grid grid-cols-[minmax(0,1fr)] gap-5">
      {/* Greeting */}
      <div className="rise flex flex-wrap items-center justify-between gap-3">
        <div className="min-w-0">
          <h1 className="truncate text-[1.75rem] font-semibold leading-tight tracking-[-0.02em] sm:text-3xl">Hello, {name}</h1>
          <p className="mt-0.5 text-[15px] text-muted">Where are we going today?</p>
        </div>
        {!locked && (
          <Link href="/drive" className="btn btn-primary !min-h-12 !px-5">
            <Navigation className="h-5 w-5" aria-hidden /> {t["nav.drive"]}
          </Link>
        )}
      </div>
      {!locked && <TopChips flags={ctx.flags} units={ctx.units} currency={ctx.profile.currency} variant="inline" />}
      {!ctx.profile.onboarded_at && (
        <p className="glass !rounded-2xl px-4 py-3 text-sm">
          Finish setting your language and units in{" "}
          <Link href="/onboarding" className="font-semibold text-accent-2 underline">onboarding</Link>.
        </p>
      )}

      <div className="grid grid-cols-[minmax(0,1fr)] gap-5 xl:grid-cols-[minmax(0,1fr)_360px]">
        {/* Map workspace */}
        <div className="grid min-w-0 grid-cols-[minmax(0,1fr)] content-start gap-4">
          <GlassPanel accent className="rise-2 relative overflow-hidden !rounded-[28px] p-1.5">
            <div className="relative h-[300px] overflow-hidden rounded-[22px] sm:h-[420px] xl:h-[clamp(520px,calc(100dvh-21rem),820px)]">
              <MiniMap
                config={map.mapConfig}
                center={latest ? [latest.lon, latest.lat] : map.initialCenter}
                pins={latest ? [[latest.lon, latest.lat]] : []}
              />
              {/* search entry */}
              <Link
                href={locked ? "/account/billing" : "/app/map?focus=search"}
                className="glass-strong absolute left-3 right-3 top-3 flex h-12 items-center gap-3 !rounded-2xl px-4 text-muted transition-colors hover:text-text sm:left-4 sm:right-auto sm:top-4 sm:w-[min(420px,60%)]"
              >
                <Search className="h-5 w-5 shrink-0" aria-hidden />
                <span className="truncate">{t["search.placeholder"]}</span>
              </Link>
              {map.mapConfig.notice && (
                <p className="chip absolute bottom-3 right-3 hidden max-w-[42%] !whitespace-normal bg-panel-strong text-xs leading-snug text-muted sm:inline-flex">
                  {map.mapConfig.notice}
                </p>
              )}
              {/* last destination */}
              {latest ? (
                <Link
                  href={locked ? "/account/billing" : mapHref(latest)}
                  className="glass-strong absolute bottom-3 left-3 flex max-w-[calc(100%-1.5rem)] items-center gap-3 !rounded-2xl py-2.5 pl-3 pr-4 transition-transform hover:-translate-y-0.5 sm:bottom-4 sm:left-4"
                >
                  <span className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-gradient-to-b from-[#3d93ff] to-[#1260e6] text-white">
                    <MapPin className="h-5 w-5" aria-hidden />
                  </span>
                  <span className="min-w-0">
                    <span className="block text-xs text-muted">Last destination</span>
                    <span className="block truncate font-semibold">{latest.name}</span>
                  </span>
                  <ChevronRight className="h-4 w-4 shrink-0 text-muted" aria-hidden />
                </Link>
              ) : (
                <Link href={locked ? "/account/billing" : "/app/map"} className="btn btn-primary absolute bottom-4 left-4 !min-h-12">
                  Open full map <ArrowRight className="h-4 w-4" aria-hidden />
                </Link>
              )}
            </div>
          </GlassPanel>

          {/* Quick actions dock */}
          <nav aria-label="Quick actions" className="rise-3 grid grid-cols-2 gap-3 sm:grid-cols-4">
            {quick.map((q) => (
              <Link
                key={q.href}
                href={locked && q.href !== "/app/saved" ? "/account/billing" : q.href}
                className="glass lift flex items-center gap-3 !rounded-[20px] p-3 sm:flex-col sm:gap-2.5 sm:py-4"
              >
                <IconTile tint={q.tint} size="sm">
                  <q.icon className="h-5 w-5" aria-hidden />
                </IconTile>
                <span className="text-[15px] font-semibold">{q.label}</span>
              </Link>
            ))}
          </nav>

          {recent.length > 1 && (
            <section aria-labelledby="recent-title" className="rise-3 min-w-0">
              <h2 id="recent-title" className="eyebrow mb-2 px-1">Recent</h2>
              <ul className="flex gap-2 overflow-x-auto pb-1">
                {recent.map((r) => (
                  <li key={r.id} className="shrink-0">
                    <Link href={locked ? "/account/billing" : mapHref(r)} className="chip !py-2 text-[13px] hover:border-line-strong">
                      <Clock className="h-3.5 w-3.5 text-muted" aria-hidden /> {r.name}
                      <span className="text-faint">· {relativeTime(r.used_at)}</span>
                    </Link>
                  </li>
                ))}
              </ul>
            </section>
          )}
        </div>

        {/* Right column */}
        <div className="grid content-start gap-4 md:grid-cols-2 xl:grid-cols-1">
          <GlassCard tier="elevated" accent={!locked} className="rise-2 !rounded-[24px] md:col-span-2 xl:col-span-1">
            <div className="flex items-start gap-3">
              <span className="grid h-11 w-11 shrink-0 place-items-center rounded-2xl border border-line-strong bg-gradient-to-br from-[#1d4c8f] to-[#081a3a]">
                <LogoMark size={26} />
              </span>
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center justify-between gap-x-2 gap-y-1">
                  <p className="font-semibold">MAPIO Premium</p>
                  <StatusPill status={entitlement.status} label={statusLabel} />
                </div>
                <p className="mt-0.5 text-sm text-muted">{planLine}</p>
              </div>
            </div>
            <div className="mt-4">
              {entitlement.hasAccess ? (
                <Link href="/account/billing" className="btn btn-ghost w-full !min-h-11">
                  Manage subscription
                </Link>
              ) : (
                <CheckoutButton label={billing.trialEligible ? `Start ${billing.trialDays}-day free trial` : "Subscribe"} className="w-full !min-h-12 !text-base" />
              )}
            </div>
          </GlassCard>

          <GlassCard
            className="rise-3 !rounded-[24px]"
            title={
              <span className="flex items-center gap-2">
                {t["nav.inbox"]}
                {inbox.filter((i) => i.state === "sent" || i.state === "received").length > 0 && (
                  <span className="grid h-5 min-w-5 place-items-center rounded-full bg-accent-2 px-1.5 text-[11px] font-bold text-[#04223a]">
                    {inbox.filter((i) => i.state === "sent" || i.state === "received").length}
                  </span>
                )}
              </span>
            }
            action={<Link href="/app/inbox" className="text-sm font-semibold text-accent-2">View all</Link>}
          >
            {inbox.length === 0 ? (
              <p className="text-sm text-muted">
                <Link href="/app/devices" className="font-semibold text-accent-2 underline">Pair your car browser</Link>, then send destinations from your phone.
              </p>
            ) : (
              <ul className="-mx-2 grid">
                {inbox.map((i) => (
                  <li key={i.id}>
                    <Link href={locked ? "/account/billing" : mapHref({ lat: Number(i.lat), lon: Number(i.lon), name: i.name })} className="flex items-center gap-3 rounded-xl px-2 py-2.5 transition-colors hover:bg-white/[0.05]">
                      <MapPin className="h-5 w-5 shrink-0 text-accent-2" aria-hidden />
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-[15px]">{i.name}</span>
                        <span className="block truncate text-xs text-muted">
                          Sent {relativeTime(i.sent_at)} · to {i.device_name}
                        </span>
                      </span>
                      <ChevronRight className="h-4 w-4 shrink-0 text-muted" aria-hidden />
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </GlassCard>

          <GlassCard className="rise-3 !rounded-[24px]" title={t["nav.saved"]} action={<Link href="/app/saved" className="text-sm font-semibold text-accent-2">Manage</Link>}>
            {saved.length === 0 ? (
              <p className="text-sm text-muted">Save Home, Work and favourite places for one-tap routes.</p>
            ) : (
              <ul className="-mx-2 grid">
                {saved.slice(0, 4).map((p) => {
                  const I = p.kind === "home" ? Home : p.kind === "work" ? Briefcase : Star;
                  return (
                    <li key={p.id}>
                      <Link href={locked ? "/account/billing" : mapHref(p)} className="flex items-center gap-3 rounded-xl px-2 py-2.5 transition-colors hover:bg-white/[0.05]">
                        <I className="h-5 w-5 shrink-0 text-accent-2" aria-hidden />
                        <span className="min-w-0 flex-1 truncate text-[15px]">{p.label}</span>
                        <ChevronRight className="h-4 w-4 shrink-0 text-muted" aria-hidden />
                      </Link>
                    </li>
                  );
                })}
              </ul>
            )}
          </GlassCard>
        </div>
      </div>
    </div>
  );
}
