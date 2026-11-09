import Link from "next/link";
import { ArrowRight, Check, CreditCard, Receipt, RefreshCw, Settings, SlidersHorizontal, UserRound } from "lucide-react";
import { LogoMark } from "@/components/ui/logo";
import { formatMoney } from "@/lib/format";
import type { PublicPrice } from "@/lib/billing";

export function PriceTag({ price }: { price: PublicPrice }) {
  return (
    <span>
      {formatMoney(price.amountMinor, price.currency)}
      <span className="text-2xl font-semibold text-muted">/{price.interval === "month" ? "mo" : price.interval}</span>
    </span>
  );
}

export function PricingCard({ price }: { price: PublicPrice }) {
  return (
    <section aria-labelledby="pricing-title" className="glass glow-edge relative grid gap-6 overflow-hidden p-6 sm:grid-cols-[150px_1fr] sm:items-start md:p-8">
      <div className="mx-auto grid h-32 w-32 place-items-center sm:mt-6 rounded-[32px] border border-line-strong bg-gradient-to-br from-[#163a73]/80 to-[#071a3a]/80 shadow-[0_30px_60px_-20px_var(--glow)] [transform:perspective(600px)_rotateX(12deg)_rotateY(-14deg)]">
        <LogoMark size={74} />
      </div>
      <div>
        <h2 id="pricing-title" className="text-2xl font-bold">MAPIO Premium</h2>
        <p className="mt-2 flex flex-wrap items-baseline gap-x-3 text-4xl font-bold md:text-5xl">
          <PriceTag price={price} />
          {price.trialDays > 0 && <span className="text-base font-medium text-muted">after {price.trialDays}-day free trial</span>}
        </p>
        <ul className="mt-4 grid gap-2 text-[15px] lg:grid-cols-2 lg:gap-x-6 xl:grid-cols-1 2xl:grid-cols-2">
          {[
            "Full access to all MAPIO features",
            "3D maps and turn-by-turn route guidance (provider-sourced)",
            "Public charger finder with source attribution",
            "Send to Car, weather, official GEL rates and more",
          ].map((t) => (
            <li key={t} className="flex items-start gap-2">
              <Check className="mt-0.5 h-5 w-5 shrink-0 text-accent-2" aria-hidden /> {t}
            </li>
          ))}
        </ul>
      </div>
      <div className="flex flex-col items-stretch gap-2 text-center sm:col-start-2 sm:items-start sm:text-left">
        <Link href="/signup?plan=premium" className="btn btn-primary !min-h-14 px-7 text-lg">
          Start {price.trialDays}-day free trial <ArrowRight className="h-5 w-5" aria-hidden />
        </Link>
        <p className="text-sm text-muted">
          Then {formatMoney(price.amountMinor, price.currency)}/{price.interval}. Cancel anytime.
        </p>
        {price.source === "configured" && (
          <p className="text-xs text-faint">Final price, currency and any taxes are shown at checkout.</p>
        )}
      </div>
    </section>
  );
}

/** Static illustration of the account billing screen (clearly labelled; not live data). */
export function SubscriptionIllustration() {
  const rows: [typeof Receipt, string, string][] = [
    [Receipt, "Trial ends", "in 7 days"],
    [RefreshCw, "Next renewal", "after trial"],
    [CreditCard, "Payment method", "Managed securely by Paddle"],
  ];
  return (
    <figure className="glass grid gap-4 p-4 md:grid-cols-[170px_1fr]">
      <nav aria-hidden className="flex flex-col gap-1 text-sm">
        <span className="mb-2 flex items-center gap-2 font-semibold">
          <LogoMark size={22} /> My Account
        </span>
        {[
          [SlidersHorizontal, "Subscription", true],
          [Receipt, "Billing", false],
          [UserRound, "Account", false],
          [Settings, "Settings", false],
        ].map(([I, l, on]) => {
          const Icon = I as typeof Receipt;
          return (
            <span key={l as string} className={`flex items-center gap-2 rounded-xl px-3 py-2 ${on ? "bg-accent text-white" : "text-muted"}`}>
              <Icon className="h-4 w-4" /> {l as string}
            </span>
          );
        })}
      </nav>
      <div className="rounded-2xl border border-line bg-panel-strong p-4">
        <div className="flex items-center justify-between">
          <b>MAPIO Premium</b>
          <span className="chip border-ok/40 bg-ok/15 text-ok">Free Trial</span>
        </div>
        <dl className="mt-3 grid gap-2 text-sm">
          {rows.map(([I, k, v]) => (
            <div key={k} className="flex items-center gap-2">
              <I className="h-4 w-4 text-muted" aria-hidden />
              <dt className="text-muted">{k}</dt>
              <dd className="ml-auto">{v}</dd>
            </div>
          ))}
        </dl>
        <div className="mt-4 grid grid-cols-2 gap-2">
          <span className="btn btn-primary !min-h-10 text-sm">Manage plan</span>
          <span className="btn btn-ghost !min-h-10 text-sm">Cancel subscription</span>
        </div>
      </div>
      <figcaption className="text-xs text-faint md:col-span-2">Illustration of the account billing page.</figcaption>
    </figure>
  );
}
