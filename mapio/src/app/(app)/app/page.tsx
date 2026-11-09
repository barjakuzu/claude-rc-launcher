import type { Metadata } from "next";
import Link from "next/link";
import { ArrowRight, Car, Clock, Home, Briefcase, MapPin, Navigation, Send, Star } from "lucide-react";
import { getAppContext, getMapPageData } from "@/lib/app-context";
import { listRecent, listSaved } from "@/lib/repos/places";
import { listAllInbox } from "@/lib/repos/inbox";
import { Card, PageTitle, StatusPill } from "@/components/app/card";
import { MiniMap } from "@/components/map/mini-map";
import { CheckoutButton } from "@/components/billing/billing-controls";
import { formatDate, formatMoney, relativeTime } from "@/lib/format";

export const metadata: Metadata = { title: "Dashboard" };

export default async function DashboardPage() {
  const ctx = await getAppContext();
  const { t, entitlement, billing } = ctx;
  const [map, saved, recent, inbox] = await Promise.all([
    getMapPageData(ctx.user.id),
    listSaved(ctx.user.id),
    listRecent(ctx.user.id, 5),
    listAllInbox(ctx.user.id, 5),
  ]);
  const name = ctx.profile.display_name || ctx.user.email.split("@")[0];
  const sub = billing.subscription;

  return (
    <>
      <PageTitle
        title={`Hello, ${name}`}
        sub="Your MAPIO overview"
        action={
          entitlement.hasAccess ? (
            <Link href="/drive" className="btn btn-primary !min-h-12">
              <Navigation className="h-5 w-5" aria-hidden /> {t["nav.drive"]}
            </Link>
          ) : null
        }
      />
      {!ctx.profile.onboarded_at && (
        <p className="mb-5 rounded-2xl border border-line bg-white/5 px-4 py-3 text-sm">
          Finish setting up your language and units in <Link href="/onboarding" className="font-semibold text-accent-2 underline">onboarding</Link>.
        </p>
      )}

      <div className="grid gap-5 xl:grid-cols-[1.6fr_1fr]">
        <Card className="!p-2">
          <div className="relative h-[340px]">
            <MiniMap config={map.mapConfig} center={recent[0] ? [recent[0].lon, recent[0].lat] : map.initialCenter} pins={recent[0] ? [[recent[0].lon, recent[0].lat]] : []} />
            <Link href={entitlement.hasAccess ? "/app/map" : "/account/billing"} className="btn btn-primary absolute bottom-4 left-4 !min-h-12">
              Open full map <ArrowRight className="h-4 w-4" aria-hidden />
            </Link>
            {map.mapConfig.notice && <p className="chip absolute right-3 top-3 bg-panel-strong text-xs text-muted">{map.mapConfig.notice}</p>}
          </div>
        </Card>

        <Card title="MAPIO Premium" action={<StatusPill status={entitlement.status} label={t[`plan.${entitlement.status}` as keyof typeof t] ?? entitlement.status} />}>
          {entitlement.hasAccess && sub ? (
            <dl className="grid gap-2 text-[15px]">
              {entitlement.status === "trialing" && (
                <div className="flex justify-between"><dt className="text-muted">Trial ends</dt><dd>{formatDate(sub.trial_end)}</dd></div>
              )}
              {entitlement.status === "scheduled_cancel" ? (
                <div className="flex justify-between"><dt className="text-muted">Access until</dt><dd>{formatDate(entitlement.accessUntil)}</dd></div>
              ) : (
                <div className="flex justify-between"><dt className="text-muted">Next charge</dt><dd>{formatDate(sub.next_billed_at)}</dd></div>
              )}
              {sub.unit_price_amount != null && sub.currency_code && (
                <div className="flex justify-between"><dt className="text-muted">Amount</dt><dd>{formatMoney(Number(sub.unit_price_amount), sub.currency_code)}/month</dd></div>
              )}
              <Link href="/account/billing" className="btn btn-ghost mt-3">Manage plan</Link>
            </dl>
          ) : entitlement.hasAccess ? (
            <p className="text-muted">Access granted by MAPIO support until {formatDate(entitlement.accessUntil)}.</p>
          ) : (
            <div className="grid gap-3">
              <p className="text-muted">
                {billing.trialEligible ? `Try every feature free for ${billing.trialDays} days.` : "Subscribe to unlock maps, routing and Send to Car."}
              </p>
              <CheckoutButton label={billing.trialEligible ? `Start ${billing.trialDays}-day free trial` : "Subscribe"} />
            </div>
          )}
        </Card>

        <Card title={t["nav.saved"]} action={<Link href="/app/saved" className="text-sm font-semibold text-accent-2">Manage</Link>}>
          {saved.length === 0 ? (
            <p className="text-muted">Save Home, Work and favourite places for one-tap routes.</p>
          ) : (
            <ul className="grid gap-1">
              {saved.slice(0, 5).map((p) => {
                const I = p.kind === "home" ? Home : p.kind === "work" ? Briefcase : Star;
                return (
                  <li key={p.id}>
                    <Link href={`/app/map?lat=${p.lat}&lon=${p.lon}&name=${encodeURIComponent(p.name)}`} className="flex items-center gap-3 rounded-xl px-2 py-2.5 hover:bg-white/5">
                      <I className="h-5 w-5 text-accent-2" aria-hidden />
                      <span className="min-w-0 flex-1 truncate">{p.label}</span>
                      <ArrowRight className="h-4 w-4 text-muted" aria-hidden />
                    </Link>
                  </li>
                );
              })}
            </ul>
          )}
        </Card>

        <Card title={t["nav.inbox"]} action={<Link href="/app/inbox" className="text-sm font-semibold text-accent-2">Open</Link>}>
          {inbox.length === 0 ? (
            <p className="text-muted">
              <Link href="/app/devices" className="font-semibold text-accent-2 underline">Pair your car browser</Link>, then send destinations from your phone.
            </p>
          ) : (
            <ul className="grid gap-1">
              {inbox.map((i) => (
                <li key={i.id} className="flex items-center gap-3 rounded-xl px-2 py-2.5">
                  <Send className="h-5 w-5 text-accent-2" aria-hidden />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate">{i.name}</span>
                    <span className="block text-xs text-muted">
                      to {i.device_name} · {relativeTime(i.sent_at)} · {i.state}
                    </span>
                  </span>
                </li>
              ))}
            </ul>
          )}
        </Card>

        <Card title="Recent destinations" className="xl:col-span-2">
          {recent.length === 0 ? (
            <p className="text-muted">Destinations you route to appear here. You can turn this off in Account.</p>
          ) : (
            <ul className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
              {recent.map((r) => (
                <li key={r.id}>
                  <Link href={`/app/map?lat=${r.lat}&lon=${r.lon}&name=${encodeURIComponent(r.name)}`} className="flex items-center gap-3 rounded-xl border border-line px-3 py-3 hover:bg-white/5">
                    <MapPin className="h-5 w-5 text-accent-2" aria-hidden />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate font-medium">{r.name}</span>
                      <span className="flex items-center gap-1 text-xs text-muted"><Clock className="h-3 w-3" aria-hidden /> {relativeTime(r.used_at)}</span>
                    </span>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
      <p className="mt-6 flex items-center gap-2 text-xs text-faint">
        <Car className="h-4 w-4" aria-hidden /> MAPIO is independent and does not connect to vehicle systems.
      </p>
    </>
  );
}
