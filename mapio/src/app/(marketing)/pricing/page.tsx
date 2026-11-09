import type { Metadata } from "next";
import { MarketingShell, PageIntro } from "@/components/marketing/shell";
import { PricingCard } from "@/components/marketing/pricing";
import { getPublicPrice } from "@/lib/billing";
import { formatMoney } from "@/lib/format";

export const revalidate = 3600;
export const metadata: Metadata = {
  title: "Pricing",
  description: "One plan: MAPIO Premium, monthly, with a free trial. Cancel anytime.",
};

export default async function PricingPage() {
  const price = await getPublicPrice();
  const amount = formatMoney(price.amountMinor, price.currency);
  return (
    <MarketingShell trialDays={price.trialDays}>
      <PageIntro eyebrow="Pricing" title="One simple plan">
        MAPIO Premium is a monthly subscription with a {price.trialDays}-day free trial. No yearly plan, no hidden tiers.
      </PageIntro>
      <div className="mx-auto max-w-[1100px] px-4">
        <PricingCard price={price} />
        <div className="mt-10 grid gap-4 md:grid-cols-2">
          {[
            [
              "How the trial works",
              `Start your ${price.trialDays}-day free trial at checkout with a payment method. You will not be charged during the trial. If you don't cancel before the trial ends, your first charge of ${amount} happens on the day the trial ends.`,
            ],
            [
              "Renewal",
              `After the trial, MAPIO Premium renews automatically every ${price.interval} at ${amount} (plus any applicable taxes shown at checkout) until you cancel.`,
            ],
            [
              "Cancel anytime",
              "Cancel from Account → Billing in one click, or via the Paddle customer portal. Cancellation takes effect at the end of your current trial or billing period, and you keep access until then. You can undo a pending cancellation before it takes effect.",
            ],
            [
              "Payments & invoices",
              "Payments are processed by Paddle, our merchant of record, which handles taxes and invoices. MAPIO never sees or stores your full card details. Invoices are available from the Paddle portal linked in your account.",
            ],
            [
              "One trial per customer",
              "The free trial is available once per customer. If you subscribe again later, billing starts immediately.",
            ],
            [
              "Price shown here",
              price.source === "provider"
                ? "The price above is read from our payment provider. The final amount including tax and your local currency is confirmed at checkout."
                : "The final price, currency and any applicable taxes are confirmed at checkout before you start your trial.",
            ],
          ].map(([h, b]) => (
            <section key={h} className="glass p-6">
              <h2 className="text-lg font-semibold">{h}</h2>
              <p className="mt-2 leading-relaxed text-muted">{b}</p>
            </section>
          ))}
        </div>
      </div>
    </MarketingShell>
  );
}
