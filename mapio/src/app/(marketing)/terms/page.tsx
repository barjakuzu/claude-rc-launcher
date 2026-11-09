import type { Metadata } from "next";
import { LegalDraftBanner, MarketingShell, PageIntro } from "@/components/marketing/shell";

export const metadata: Metadata = { title: "Terms of Service", robots: { index: false } };

const sections: [string, string][] = [
  ["1. Who we are", "MAPIO is provided by [COMPANY LEGAL NAME], [REGISTERED ADDRESS], [COMPANY REGISTRATION NUMBER] (“MAPIO”, “we”). Contact: [SUPPORT EMAIL]."],
  ["2. The service", "MAPIO is a web-based map and navigation application. It is provided for general route planning and information. Map, routing, charger, weather and exchange-rate data come from third-party providers and may be incomplete, inaccurate or out of date."],
  ["3. Road safety", "You are solely responsible for operating your vehicle safely and lawfully. Always follow road signs, traffic laws and local conditions over any instruction shown by MAPIO. Do not interact with MAPIO in a way that distracts you while driving. MAPIO does not provide live traffic or lane guidance."],
  ["4. No vehicle integration; no affiliation", "MAPIO is independent and is not affiliated with, endorsed by or sponsored by Tesla, Inc. or any vehicle manufacturer. MAPIO does not access or control vehicle systems. “Send to Car” delivers destinations to MAPIO open in a browser you have paired, not to built-in vehicle navigation."],
  ["5. Accounts", "You must provide accurate information and keep your credentials secure. You are responsible for activity under your account and for devices you pair. You can revoke paired devices at any time."],
  ["6. Subscription, free trial and renewal", "MAPIO Premium is a monthly subscription sold by our reseller and merchant of record, Paddle.com Market Ltd (“Paddle”), whose buyer terms also apply. A free trial of [TRIAL LENGTH] days may be offered once per customer; a payment method is required. Unless you cancel before the trial ends, you will be charged the price shown at checkout when the trial ends and every month thereafter until you cancel. Prices may include or exclude taxes as shown at checkout."],
  ["7. Cancellation and refunds", "You can cancel at any time in Account → Billing or through the Paddle customer portal. Cancellation takes effect at the end of the current trial or billing period. [REFUND POLICY — TO BE CONFIRMED WITH COUNSEL AND PADDLE POLICY; STATUTORY WITHDRAWAL RIGHTS FOR CONSUMERS MAY APPLY.]"],
  ["8. Acceptable use", "Do not misuse the service, including scraping, reverse engineering, automated excessive requests, attempting to access other users' data, or circumventing subscription controls. We may suspend accounts that do so."],
  ["9. Third-party data and attribution", "Map data © OpenStreetMap contributors and other providers credited in the app. Third-party data is subject to its own licences."],
  ["10. Disclaimers and limitation of liability", "[TO BE DRAFTED BY COUNSEL FOR THE APPLICABLE JURISDICTION(S).]"],
  ["11. Changes", "We may update these terms. Material changes will be notified in advance by email or in the app."],
  ["12. Governing law", "[GOVERNING LAW AND JURISDICTION — TO BE CONFIRMED.]"],
];

export default function TermsPage() {
  return (
    <MarketingShell>
      <PageIntro eyebrow="Legal" title="Terms of Service">Last updated: [DATE]</PageIntro>
      <LegalDraftBanner />
      <article className="mx-auto max-w-[900px] space-y-6 px-4">
        {sections.map(([h, b]) => (
          <section key={h}>
            <h2 className="text-xl font-semibold">{h}</h2>
            <p className="mt-2 leading-relaxed text-muted">{b}</p>
          </section>
        ))}
      </article>
    </MarketingShell>
  );
}
