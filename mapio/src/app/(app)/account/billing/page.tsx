import type { Metadata } from "next";
import { CalendarClock, CreditCard, Receipt, RefreshCw, Wallet } from "lucide-react";
import { getAppContext } from "@/lib/app-context";
import { PageTitle, StatusPill } from "@/components/app/card";
import { GlassPanel } from "@/components/ui/glass";
import { AwaitEntitlement, CancelControls, CheckoutButton, PortalButton } from "@/components/billing/billing-controls";
import { formatDate, formatMoney } from "@/lib/format";
import { sp, type SearchParams } from "@/lib/page";
import { LogoMark } from "@/components/ui/logo";

export const metadata: Metadata = { title: "Your Subscription" };

function Fact({ icon: I, label, value, note, wide }: { icon: typeof Receipt; label: string; value: string; note?: string; wide?: boolean }) {
  return (
    <div className={`min-w-0 rounded-2xl border border-line bg-white/[0.035] px-3.5 py-3 ${wide ? "col-span-2" : ""}`}>
      <dt className="flex items-center gap-1.5 text-xs text-muted">
        <I className="h-3.5 w-3.5 shrink-0" aria-hidden /> {label}
      </dt>
      <dd className="mt-1 font-semibold leading-snug">{value}</dd>
      {note && <dd className="mt-0.5 text-xs leading-snug text-faint">{note}</dd>}
    </div>
  );
}

export default async function BillingPage({ searchParams }: { searchParams: SearchParams }) {
  const ctx = await getAppContext();
  const q = await searchParams;
  const { billing, entitlement, t } = ctx;
  const sub = billing.subscription;
  const price = billing.displayPrice;
  const statusLabel = t[`plan.${entitlement.status}` as keyof typeof t] ?? entitlement.status;
  const waiting = sp(q.checkout) === "complete" && !entitlement.hasAccess;
  const amount =
    sub?.unit_price_amount != null && sub.currency_code
      ? formatMoney(Number(sub.unit_price_amount), sub.currency_code)
      : formatMoney(price.amountMinor, price.currency);
  const live = !!sub && ["trialing", "active", "past_due", "scheduled_cancel"].includes(entitlement.status);
  const canSubscribe = (!sub || !entitlement.hasAccess) && entitlement.status !== "past_due" && !waiting;

  return (
    <div className="grid max-w-3xl grid-cols-[minmax(0,1fr)] gap-4">
      <PageTitle title="Your Subscription" sub="MAPIO Premium, billed securely by Paddle." />

      {billing.mode === "demo" && (
        <p className="rounded-2xl border border-warn/40 bg-warn/10 px-4 py-2.5 text-sm text-warn">
          <b>Development simulator.</b> Paddle isn&apos;t configured, so no real payments are taken.
        </p>
      )}
      {billing.environment === "sandbox" && billing.mode === "live" && (
        <p className="rounded-2xl border border-warn/40 bg-warn/10 px-4 py-2.5 text-sm text-warn">Paddle sandbox: use Paddle test cards only.</p>
      )}
      {waiting && <AwaitEntitlement />}
      {sp(q.checkout) === "activated" && (
        <p className="rounded-2xl border border-ok/40 bg-ok/10 px-4 py-2.5 text-sm text-ok">Your subscription is active. Enjoy MAPIO!</p>
      )}

      <GlassPanel as="section" tier="elevated" accent={live} className="rise-2 !rounded-[26px] p-5 sm:p-6" aria-label="Plan">
        <div className="flex items-center gap-4">
          <span className="grid h-14 w-14 shrink-0 place-items-center rounded-[18px] border border-line-strong bg-gradient-to-br from-[#1d4c8f] to-[#081a3a] shadow-[inset_0_1px_0_rgba(255,255,255,0.2)]">
            <LogoMark size={32} />
          </span>
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
              <h2 className="text-xl font-semibold tracking-tight">MAPIO Premium</h2>
              <StatusPill status={entitlement.status} label={statusLabel} />
            </div>
            <p className="text-muted">{amount} / month</p>
          </div>
        </div>

        {sub ? (
          <dl className="mt-5 grid grid-cols-2 gap-2.5">
            {entitlement.status === "trialing" && sub.trial_end && <Fact icon={CalendarClock} label="Trial ends" value={formatDate(sub.trial_end)} />}
            {entitlement.status === "scheduled_cancel" && (
              <Fact icon={CalendarClock} label="Access until" value={formatDate(entitlement.accessUntil)} note="No further charges" />
            )}
            {entitlement.status === "past_due" && (
              <Fact icon={CreditCard} label="Payment" value="Renewal failed" note={`Access until ${formatDate(entitlement.accessUntil)}`} />
            )}
            {entitlement.status === "canceled" && sub.canceled_at && <Fact icon={CalendarClock} label="Ended" value={formatDate(sub.canceled_at)} />}
            {sub.next_billed_at && !["canceled", "scheduled_cancel"].includes(entitlement.status) && (
              <Fact icon={RefreshCw} label="Next billing" value={formatDate(sub.next_billed_at)} note={`${amount} plus any tax`} />
            )}
            {entitlement.status === "active" && sub.current_period_end && <Fact icon={Receipt} label="Period ends" value={formatDate(sub.current_period_end)} />}
            <Fact icon={Wallet} label="Payment method" value="Held securely by Paddle" note="MAPIO never stores card details" wide />
          </dl>
        ) : (
          <p className="mt-5 text-muted">
            You don&apos;t have a subscription yet.{" "}
            {billing.trialEligible ? `Start with ${billing.trialDays} days free, then ${amount}/month.` : `Subscribe for ${amount}/month.`}
          </p>
        )}

        <div className="mt-5 grid gap-2.5 sm:grid-cols-2">
          {canSubscribe && (
            <div className="sm:col-span-2">
              <CheckoutButton label={billing.trialEligible ? `Start ${billing.trialDays}-day free trial` : "Subscribe"} className="w-full !min-h-[3.25rem] !text-base" />
            </div>
          )}
          {sub && (
            <PortalButton
              variant={entitlement.status === "past_due" ? "primary" : "ghost"}
              label={entitlement.status === "past_due" ? "Update payment method" : "Manage subscription"}
              className="sm:col-span-2"
            />
          )}
          {sub && entitlement.status !== "past_due" && (
            <>
              <PortalButton label="Billing history" icon={<Receipt className="h-4 w-4" aria-hidden />} />
              <PortalButton label="Change payment method" icon={<CreditCard className="h-4 w-4" aria-hidden />} />
            </>
          )}
          {live && (
            <div className="sm:col-span-2">
              <CancelControls
                scheduled={entitlement.status === "scheduled_cancel"}
                accessUntilLabel={formatDate(sub!.trial_end && entitlement.status === "trialing" ? sub!.trial_end : sub!.current_period_end)}
              />
            </div>
          )}
        </div>
      </GlassPanel>

      <details className="glass group !rounded-[22px] px-5 py-4 [&_summary::-webkit-details-marker]:hidden">
        <summary className="flex cursor-pointer list-none items-center justify-between font-semibold">
          How billing works
          <span aria-hidden className="text-xl text-accent-2 transition-transform group-open:rotate-45">+</span>
        </summary>
        <ul className="mt-3 grid gap-2 text-sm text-muted">
          <li><b className="text-text">Trial:</b> {billing.trialDays} days free with a payment method; charged when the trial ends unless you cancel.</li>
          <li><b className="text-text">Renewal:</b> monthly at the price shown at checkout, until you cancel.</li>
          <li><b className="text-text">Cancel anytime:</b> takes effect at the end of the trial or billing period; you keep access until then and can undo it.</li>
          <li><b className="text-text">Invoices and payment method:</b> in the Paddle customer portal. One free trial per customer.</li>
        </ul>
      </details>
    </div>
  );
}
