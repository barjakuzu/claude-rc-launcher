import type { Metadata } from "next";
import { Mail, ShieldCheck } from "lucide-react";
import { LegalDraftBanner, MarketingShell, PageIntro } from "@/components/marketing/shell";

export const metadata: Metadata = { title: "Contact", description: "Get help with MAPIO." };

const SUPPORT = process.env.NEXT_PUBLIC_SUPPORT_EMAIL;

export default function ContactPage() {
  return (
    <MarketingShell>
      <PageIntro eyebrow="Contact" title="We’re here to help">
        Questions about your subscription, pairing or browser support? Get in touch.
      </PageIntro>
      {!SUPPORT && <LegalDraftBanner />}
      <div className="mx-auto grid max-w-[1100px] gap-4 px-4 md:grid-cols-2">
        <section className="glass p-6">
          <h2 className="flex items-center gap-2 text-lg font-semibold"><Mail className="h-5 w-5 text-accent-2" aria-hidden /> Support</h2>
          <p className="mt-2 text-muted">
            Email{" "}
            {SUPPORT ? (
              <a className="font-semibold text-accent-2 underline" href={`mailto:${SUPPORT}`}>{SUPPORT}</a>
            ) : (
              <span className="font-semibold">[SUPPORT EMAIL — set NEXT_PUBLIC_SUPPORT_EMAIL]</span>
            )}
            . Please don’t include payment card details.
          </p>
        </section>
        <section className="glass p-6">
          <h2 className="flex items-center gap-2 text-lg font-semibold"><ShieldCheck className="h-5 w-5 text-accent-2" aria-hidden /> Billing questions</h2>
          <p className="mt-2 text-muted">
            Payments are handled by Paddle, our merchant of record. For receipts or payment changes, use the customer
            portal link in Account → Billing, or contact Paddle via the link in your receipt email.
          </p>
        </section>
      </div>
    </MarketingShell>
  );
}
