import Link from "next/link";
import { ArrowRight, Check, CreditCard, Receipt, RefreshCw, Settings, SlidersHorizontal, UserRound } from "lucide-react";
import { LogoMark } from "@/components/ui/logo";
import { formatMoney } from "@/lib/format";
import type { PublicPrice } from "@/lib/billing";

/** Glass "app icon" block used by the pricing card. */
export function PlanMark({ size = 150 }: { size?: number }) {
  return (
    <div className="relative mx-auto" style={{ width: size, height: size }}>
      <div aria-hidden className="absolute inset-x-[-10%] bottom-[-14%] h-[40%] rounded-[100%] bg-[radial-gradient(closest-side,rgba(60,170,255,0.7),transparent)] blur-xl" />
      <div className="relative grid h-full w-full place-items-center rounded-[32%] border border-[rgba(170,215,255,0.45)] bg-gradient-to-br from-[#1d4c8f]/90 via-[#0f2c5c]/90 to-[#081a3a]/90 shadow-[0_30px_60px_-24px_rgba(0,10,40,0.9),inset_0_1px_0_rgba(255,255,255,0.3)] [transform:perspective(700px)_rotateX(14deg)_rotateY(-16deg)]">
        <span aria-hidden className="absolute inset-x-[10%] top-[6%] h-[38%] rounded-t-[28%] bg-gradient-to-b from-white/25 to-transparent" />
        <LogoMark size={Math.round(size * 0.52)} />
      </div>
    </div>
  );
}

export function PriceTag({ price }: { price: PublicPrice }) {
  return (
    <span className="whitespace-nowrap">
      {formatMoney(price.amountMinor, price.currency)}
      <span className="text-[0.55em] font-semibold text-muted">/{price.interval === "month" ? "mo" : price.interval}</span>
    </span>
  );
}

const INCLUDED = [
  "Full access to all MAPIO features",
  "3D maps and route guidance from our routing provider",
  "Public charger finder with source attribution",
  "Send to Car, weather and official GEL rates",
];

export function PricingCard({ price }: { price: PublicPrice }) {
  return (
    <section aria-labelledby="pricing-title" className="glass-elevated glass-accent relative grid gap-6 overflow-hidden !rounded-[28px] p-6 sm:grid-cols-[170px_1fr] sm:items-center md:p-8">
      <div aria-hidden className="pointer-events-none absolute -left-20 -top-24 h-64 w-64 rounded-full bg-[radial-gradient(closest-side,rgba(54,199,255,0.18),transparent)]" />
      <PlanMark size={150} />
      <div className="relative min-w-0">
        <h2 id="pricing-title" className="text-2xl font-semibold tracking-tight">MAPIO Premium</h2>
        <p className="mt-1 flex flex-wrap items-baseline gap-x-3 text-5xl font-semibold tracking-tight">
          <PriceTag price={price} />
          {price.trialDays > 0 && <span className="text-base font-medium tracking-normal text-muted">after {price.trialDays}-day free trial</span>}
        </p>
        <div className="mt-5 grid gap-6 2xl:grid-cols-[1fr_auto] 2xl:items-end">
          <ul className="grid gap-2.5 text-[15px]">
            {INCLUDED.map((t) => (
              <li key={t} className="flex items-start gap-2.5">
                <Check className="mt-0.5 h-5 w-5 shrink-0 text-accent-2" aria-hidden /> {t}
              </li>
            ))}
          </ul>
          <div className="grid justify-items-start gap-2 text-left 2xl:justify-items-center 2xl:text-center">
            <Link href="/signup?plan=premium" className="btn btn-primary !min-h-14 !px-7 text-lg">
              Start {price.trialDays}-day free trial <ArrowRight className="h-5 w-5" aria-hidden />
            </Link>
            <p className="text-sm text-muted">Then {formatMoney(price.amountMinor, price.currency)}/month. Cancel anytime.</p>
            {price.source === "configured" && <p className="text-xs text-faint">Final price and any taxes are shown at checkout.</p>}
          </div>
        </div>
      </div>
    </section>
  );
}

/** Static illustration of the account billing screen (labelled; no live data, no real dates). */
export function SubscriptionIllustration() {
  const rows: [typeof Receipt, string, string][] = [
    [Receipt, "Trial ends", "in 7 days"],
    [RefreshCw, "Next renewal", "when trial ends"],
    [CreditCard, "Payment method", "Managed by Paddle"],
  ];
  const nav: [typeof Receipt, string, boolean][] = [
    [SlidersHorizontal, "Subscription", true],
    [Receipt, "Billing", false],
    [UserRound, "Account", false],
    [Settings, "Settings", false],
  ];
  return (
    <figure className="glass grid gap-4 !rounded-[26px] p-4 sm:grid-cols-[160px_1fr]">
      <nav aria-hidden className="hidden flex-col gap-1 text-sm sm:flex">
        <span className="mb-2 flex items-center gap-2 px-2 font-semibold">
          <LogoMark size={22} /> My Account
        </span>
        {nav.map(([Icon, l, on]) => (
          <span key={l} className={`flex items-center gap-2 rounded-xl px-3 py-2 ${on ? "bg-gradient-to-b from-[#3d93ff] to-[#1f6fff] text-white shadow-[0_8px_20px_-8px_var(--glow)]" : "text-muted"}`}>
            <Icon className="h-4 w-4" /> {l}
          </span>
        ))}
      </nav>
      <div className="glass-strong !rounded-[20px] p-4">
        <div className="flex items-center justify-between gap-2">
          <b className="font-semibold">MAPIO Premium</b>
          <span className="chip !border-ok/40 text-ok">Free trial</span>
        </div>
        <dl className="mt-3 grid gap-2.5 text-sm">
          {rows.map(([I, k, v]) => (
            <div key={k} className="flex items-center gap-2">
              <I className="h-4 w-4 shrink-0 text-muted" aria-hidden />
              <dt className="text-muted">{k}</dt>
              <dd className="ml-auto text-right">{v}</dd>
            </div>
          ))}
        </dl>
        <div className="mt-4 grid grid-cols-2 gap-2">
          <span className="btn btn-primary !min-h-10 !px-3 text-sm">Manage plan</span>
          <span className="btn btn-ghost !min-h-10 !px-3 text-sm">Cancel</span>
        </div>
      </div>
      <figcaption className="text-xs text-faint sm:col-span-2">Illustration of the account billing page.</figcaption>
    </figure>
  );
}
