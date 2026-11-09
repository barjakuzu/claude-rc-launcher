import Link from "next/link";
import { Lock } from "lucide-react";
import { CheckoutButton } from "@/components/billing/billing-controls";
import type { BillingState } from "@/lib/billing";
import { formatMoney } from "@/lib/format";

/** Shown in place of premium features when there is no active trial/subscription. */
export function Paywall({ billing, feature }: { billing: BillingState; feature: string }) {
  const price = formatMoney(billing.displayPrice.amountMinor, billing.displayPrice.currency);
  const status = billing.entitlement.status;
  return (
    <section className="glass glow-edge mx-auto max-w-2xl p-8 text-center">
      <span className="mx-auto grid h-14 w-14 place-items-center rounded-2xl bg-accent/20 text-accent-2">
        <Lock className="h-7 w-7" aria-hidden />
      </span>
      <h1 className="mt-4 text-2xl font-bold">{feature} is part of MAPIO Premium</h1>
      <p className="mx-auto mt-2 max-w-md text-muted">
        {status === "past_due"
          ? "Your last payment didn't go through. Update your payment method to restore access."
          : status === "canceled" || status === "paused"
            ? "Your subscription has ended. Resubscribe to continue."
            : billing.trialEligible
              ? `Start your ${billing.trialDays}-day free trial. Then ${price}/month, cancel anytime.`
              : `Subscribe for ${price}/month. Cancel anytime.`}
      </p>
      <div className="mx-auto mt-6 max-w-sm">
        {status === "past_due" ? (
          <Link href="/account/billing" className="btn btn-primary w-full !min-h-14">Fix payment</Link>
        ) : (
          <CheckoutButton label={billing.trialEligible ? `Start ${billing.trialDays}-day free trial` : "Subscribe"} className="w-full" />
        )}
      </div>
      {billing.mode === "demo" && (
        <p className="mt-4 text-xs text-warn">Development mode: checkout uses a local billing simulator. No payment is taken.</p>
      )}
    </section>
  );
}
