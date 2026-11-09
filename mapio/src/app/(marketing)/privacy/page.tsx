import type { Metadata } from "next";
import { LegalDraftBanner, MarketingShell, PageIntro } from "@/components/marketing/shell";

export const metadata: Metadata = { title: "Privacy Policy", robots: { index: false } };

const sections: [string, string][] = [
  ["Controller", "[COMPANY LEGAL NAME], [REGISTERED ADDRESS]. Privacy contact: [PRIVACY EMAIL]. [DPO / EU-UK REPRESENTATIVE IF REQUIRED]."],
  ["Location data", "If you allow it, your browser shares your position with MAPIO in the page to show you on the map and to request routes. Route start/end coordinates are sent to our routing provider to calculate a route. We do not store a history of your live position. Weather and charger lookups use coarse, rounded coordinates. Saved places and recent destinations are stored only if you save them or keep “recent destinations” enabled, and you can delete them at any time."],
  ["Account data", "Email address, authentication data (managed by our auth provider Supabase), preferences (language, units, theme), paired devices (name, browser user-agent, last seen), saved places, recent destinations and Send-to-Car items."],
  ["Payments", "Subscriptions are processed by Paddle as merchant of record. Paddle collects payment details; MAPIO receives subscription status, dates, price/currency and a customer identifier — never full card numbers. To prevent repeated free trials we keep a one-way hash of the email address that used a trial, even after account deletion, for [RETENTION PERIOD]."],
  ["Cookies and sessions", "We use strictly necessary cookies for sign-in sessions, device pairing and security. Preferences such as theme may be stored in your browser's local storage. [ANALYTICS / CONSENT BANNER — CONFIGURE IF NON-ESSENTIAL ANALYTICS ARE ENABLED.]"],
  ["Third-party processors", "Supabase (database, authentication), Vercel (hosting), MapTiler (map tiles), Geoapify (search and routing), Open-Meteo (weather), Open Charge Map (charger data), National Bank of Georgia (exchange rates — no personal data sent), Paddle (payments), [EMAIL PROVIDER], [ERROR MONITORING PROVIDER]. [LIST REGIONS AND TRANSFER MECHANISMS.]"],
  ["Retention", "Account data is kept while your account exists. Send-to-Car items are hidden after 7 days and deleted after [N] days. Usage counters are aggregated per day. [CONFIRM RETENTION SCHEDULE.]"],
  ["Your rights", "You can export your data (Account → Export data) and permanently delete your account (Account → Delete account) at any time. Depending on where you live you may have further rights, including to object and to complain to a supervisory authority [NAME RELEVANT AUTHORITIES, e.g. Personal Data Protection Service of Georgia]."],
  ["Independence from Tesla", "MAPIO is independent and not affiliated with Tesla, Inc. MAPIO does not receive data from your vehicle."],
];

export default function PrivacyPage() {
  return (
    <MarketingShell>
      <PageIntro eyebrow="Legal" title="Privacy Policy">Last updated: [DATE]</PageIntro>
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
