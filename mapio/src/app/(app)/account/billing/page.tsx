import type { Metadata } from "next";
import { CalendarClock, CreditCard, Receipt, RefreshCw, ShieldCheck } from "lucide-react";
import { getAppContext } from "@/lib/app-context";
import { Card, PageTitle, StatusPill } from "@/components/app/card";
import { AwaitEntitlement, CancelControls, CheckoutButton, PortalButton } from "@/components/billing/billing-controls";
import { formatDate, formatMoney } from "@/lib/format";
import { sp, type SearchParams } from "@/lib/page";
import { LogoMark } from "@/components/ui/logo";

export const metadata: Metadata = { title: "Billing" };

export default async function BillingPage({ searchParams }: { searchParams: SearchParams }) {
  const ctx = await getAppContext();
  const q = await searchParams;
  const { billing, entitlement, t } = ctx;
  const sub = billing.subscription;
  const price = billing.displayPrice;
  const statusLabel = t[`plan.${entitlement.status}` as keyof typeof t] ?? entitlement.status;
  const waiting = sp(q.checkout) === "complete" && !entitlement.hasAccess;
  const amount = sub?.unit_price_amount != null && sub.currency_code ? formatMoney(Number(sub.unit_price_amount), sub.currency_code) : null;

  return (
    <>
      <PageTitle title={t["nav.billing"]} sub="Your MAPIO Premium subscription, managed securely by Paddle." />
      {billing.mode === "demo" && (
        <p className="mb-5 rounded-2xl border border-warn/50 bg-warn/10 px-4 py-3 text-sm text-warn">
          Development billing simulator — Paddle is not configured. No real payments are taken; states are driven by signed
          simulated webhooks.
        </p>
      )}
      {billing.environment === "sandbox" && billing.mode === "live" && (
        <p className="mb-5 rounded-2xl border border-warn/50 bg-warn/10 px-4 py-3 text-sm text-warn">Paddle sandbox — use Paddle test cards only.</p>
      )}
      {waiting && <div className="mb-5"><AwaitEntitlement /></div>}
      {sp(q.checkout) === "activated" && <p className="mb-5 rounded-2xl border border-ok/40 bg-ok/10 px-4 py-3 text-sm text-ok">Your subscription is active. Enjoy MAPIO!</p>}

      <div className="grid gap-5 xl:grid-cols-[1.4fr_1fr]">
        <Card>
          <div className="flex flex-wrap items-center gap-4">
            <span className="grid h-16 w-16 place-items-center rounded-2xl border border-line-strong bg-gradient-to-br from-[#163a73] to-[#071a3a]">
              <LogoMark size={38} />
            </span>
            <div className="flex-1">
              <h2 className="text-xl font-bold">MAPIO Premium</h2>
              <p className="text-muted">{amount ? `${amount} / month` : `${formatMoney(price.amountMinor, price.currency)} / month`}</p>
            </div>
            <StatusPill status={entitlement.status} label={statusLabel} />
          </div>

          {sub ? (
            <dl className="mt-6 grid gap-3 text-[15px]">
              {sub.trial_end && entitlement.status !== "canceled" && (
                <Row icon={CalendarClock} k="Trial ends" v={formatDate(sub.trial_end)} />
              )}
              {entitlement.status === "scheduled_cancel" ? (
                <Row icon={CalendarClock} k="Access until" v={formatDate(entitlement.accessUntil)} note="No further charges." />
              ) : sub.next_billed_at && entitlement.status !== "canceled" ? (
                <Row icon={RefreshCw} k="Next charge" v={`${formatDate(sub.next_billed_at)}${amount ? ` · ${amount}` : ""}`} note="Plus any applicable taxes, as shown by Paddle." />
              ) : null}
              {sub.current_period_end && entitlement.status === "active" && <Row icon={Receipt} k="Current period ends" v={formatDate(sub.current_period_end)} />}
              {entitlement.status === "past_due" && (
                <Row icon={CreditCard} k="Payment" v="Last renewal failed" note={`Access continues until ${formatDate(entitlement.accessUntil)} while payment is retried.`} />
              )}
              {sub.canceled_at && entitlement.status === "canceled" && <Row icon={CalendarClock} k="Ended" v={formatDate(sub.canceled_at)} />}
              <Row icon={ShieldCheck} k="Payment method" v="Stored securely by Paddle" />
            </dl>
          ) : (
            <p className="mt-6 text-muted">You don&apos;t have a subscription yet.</p>
          )}

          <div className="mt-6 flex flex-wrap gap-3">
            {(!sub || entitlement.status === "canceled" || entitlement.status === "paused" || !entitlement.hasAccess) && entitlement.status !== "past_due" && !waiting && (
              <CheckoutButton label={billing.trialEligible ? `Start ${billing.trialDays}-day free trial` : "Subscribe"} />
            )}
            {sub && <PortalButton label={entitlement.status === "past_due" ? "Update payment method" : "Payment method & invoices"} />}
          </div>
          {sub && ["trialing", "active", "past_due", "scheduled_cancel"].includes(entitlement.status) && (
            <div className="mt-4">
              <CancelControls scheduled={entitlement.status === "scheduled_cancel"} accessUntilLabel={formatDate(sub.trial_end && entitlement.status === "trialing" ? sub.trial_end : sub.current_period_end)} />
            </div>
          )}
        </Card>

        <Card title="How billing works">
          <ul className="grid gap-3 text-sm text-muted">
            <li>
              <b className="text-text">Trial:</b> {billing.trialDays} days free with a payment method. You&apos;re charged when the trial ends unless you cancel before then.
            </li>
            <li>
              <b className="text-text">Renewal:</b> monthly at the price shown at checkout until you cancel.
            </li>
            <li>
              <b className="text-text">Cancel anytime:</b> cancellation applies at the end of your trial or billing period; you keep access until then and can undo it.
            </li>
            <li>
              <b className="text-text">Invoices &amp; payment method:</b> managed in the Paddle customer portal. MAPIO never stores card details.
            </li>
            <li>
              <b className="text-text">One trial per customer.</b>
            </li>
          </ul>
        </Card>
      </div>
    </>
  );
}

function Row({ icon: I, k, v, note }: { icon: typeof Receipt; k: string; v: string; note?: string }) {
  return (
    <div className="flex items-start gap-3">
      <I className="mt-0.5 h-5 w-5 text-muted" aria-hidden />
      <dt className="text-muted">{k}</dt>
      <dd className="ml-auto text-right">
        {v}
        {note && <span className="block text-xs text-faint">{note}</span>}
      </dd>
    </div>
  );
}
