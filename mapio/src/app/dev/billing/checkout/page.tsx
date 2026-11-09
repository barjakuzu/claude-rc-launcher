import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { AuthCard } from "@/components/auth/auth-card";
import { DevSimulator } from "@/components/billing/dev-simulator";
import { requireUser } from "@/lib/auth";
import { billingMode } from "@/lib/env";
import { getBillingState } from "@/lib/billing";
import { formatMoney } from "@/lib/format";

export const dynamic = "force-dynamic";
export const metadata: Metadata = { title: "Billing simulator", robots: { index: false } };

export default async function DevCheckoutPage() {
  let mode: string;
  try { mode = billingMode(); } catch { notFound(); }
  if (mode !== "demo") notFound();
  const user = await requireUser("/dev/billing/checkout");
  const s = await getBillingState(user.id, user.email);
  return (
    <AuthCard title="Development checkout" subtitle="Simulates Paddle checkout. No real payment is taken.">
      <div className="rounded-2xl border border-warn/50 bg-warn/10 p-4 text-sm text-warn">
        DEV SIMULATOR — Configure Paddle sandbox credentials to use real checkout (see docs/billing.md).
      </div>
      <p className="mt-4">
        MAPIO Premium · {formatMoney(s.displayPrice.amountMinor, s.displayPrice.currency)}/month
        {s.trialEligible ? ` after a ${s.trialDays}-day free trial` : " (trial already used)"}
      </p>
      <div className="mt-6"><DevSimulator mode="checkout" /></div>
    </AuthCard>
  );
}
