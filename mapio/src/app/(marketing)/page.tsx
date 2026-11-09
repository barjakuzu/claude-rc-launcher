import Link from "next/link";
import { ArrowRight, CalendarDays, Car, Infinity as InfinityIcon, PlayCircle, UserRound } from "lucide-react";
import { MarketingShell } from "@/components/marketing/shell";
import { ProductPreview } from "@/components/marketing/product-preview";
import { FEATURES, FeatureTile } from "@/components/marketing/features";
import { PricingCard, SubscriptionIllustration } from "@/components/marketing/pricing";
import { FaqList, FAQ_ITEMS } from "@/components/marketing/faq";
import { getPublicPrice } from "@/lib/billing";

export const revalidate = 3600;

export default async function LandingPage() {
  const price = await getPublicPrice();
  const jsonLd = {
    "@context": "https://schema.org",
    "@type": "SoftwareApplication",
    name: "MAPIO",
    applicationCategory: "TravelApplication",
    operatingSystem: "Web browser",
    offers: { "@type": "Offer", price: (price.amountMinor / 100).toFixed(2), priceCurrency: price.currency },
  };

  return (
    <MarketingShell trialDays={price.trialDays}>
      <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: JSON.stringify(jsonLd) }} />

      {/* Hero */}
      <section className="mx-auto grid max-w-[1440px] items-center gap-12 px-4 pb-12 pt-10 sm:px-6 lg:grid-cols-[0.95fr_1.15fr] lg:gap-8 lg:pt-16 xl:px-10">
        <div className="rise max-w-[600px]">
          <p className="chip mb-6 !py-1.5 text-[13px]">
            <Car className="h-4 w-4 text-accent-2" aria-hidden /> Built for in-car browsers
          </p>
          <h1 className="text-[2.6rem] font-semibold leading-[1.04] tracking-[-0.035em] text-balance sm:text-[3.4rem] xl:text-[3.75rem]">
            3D navigation for your <span className="text-gradient">Tesla browser</span>
          </h1>
          <p className="mt-5 max-w-[34rem] text-[17px] leading-relaxed text-muted">
            Clear 3D maps, nearby chargers, send-to-car from your phone, weather and official GEL exchange rates, plus
            simple subscription controls, all in MAPIO.
          </p>
          <div className="mt-8 flex flex-wrap gap-3">
            <Link href="/signup" className="btn btn-primary !min-h-[3.25rem] !px-6 text-[17px]">
              Start {price.trialDays}-day free trial <ArrowRight className="h-5 w-5" aria-hidden />
            </Link>
            <Link href="/how-it-works" className="btn btn-ghost !min-h-[3.25rem] !px-5 text-[17px]">
              <PlayCircle className="h-5 w-5" aria-hidden /> See how it works
            </Link>
          </div>
          <p className="mt-5 text-xs text-faint">Independent product, not affiliated with Tesla, Inc.</p>
        </div>
        <div className="rise-2">
          <ProductPreview />
        </div>
      </section>

      {/* Trust bar */}
      <section aria-label="Highlights" className="mx-auto max-w-[1440px] px-4 sm:px-6 xl:px-10">
        <ul className="glass grid gap-y-3.5 !rounded-[22px] px-5 py-5 text-[15px] sm:grid-cols-2 md:grid-cols-4 md:divide-x md:divide-line">
          {[
            [Car, "Works in in-car browsers"],
            [CalendarDays, `${price.trialDays}-day free trial`],
            [InfinityIcon, "Cancel anytime"],
            [UserRound, "Manage subscription anytime"],
          ].map(([I, t]) => {
            const Icon = I as typeof Car;
            return (
              <li key={t as string} className="flex items-center gap-3 px-2 md:justify-center">
                <Icon className="h-6 w-6 shrink-0 text-text/80" aria-hidden /> {t as string}
              </li>
            );
          })}
        </ul>
      </section>

      {/* Features */}
      <section id="features" aria-labelledby="features-title" className="mx-auto mt-16 grid max-w-[1440px] gap-8 px-4 sm:px-6 xl:px-10 wide:grid-cols-[260px_1fr] wide:items-center">
        <div>
          <h2 id="features-title" className="text-3xl font-semibold leading-tight tracking-tight text-balance">
            Everything you need for a better trip
          </h2>
          <p className="mt-3 max-w-md text-muted">A focused navigation experience for the big screen in your car: readable, fast, and calm.</p>
          <Link href="/features" className="mt-4 inline-flex items-center gap-1 font-semibold text-accent-2 hover:underline">
            All features <ArrowRight className="h-4 w-4" aria-hidden />
          </Link>
        </div>
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 wide:grid-cols-6">
          {FEATURES.map((f) => (
            <FeatureTile key={f.title} {...f} />
          ))}
        </div>
      </section>

      {/* Pricing + subscription */}
      <section id="pricing" className="mx-auto mt-16 grid max-w-[1440px] gap-6 px-4 sm:px-6 xl:grid-cols-[1.2fr_1fr] xl:items-center xl:px-10">
        <PricingCard price={price} />
        <div className="grid gap-5 md:grid-cols-[1fr_1.4fr] md:items-center xl:grid-cols-1">
          <div className="px-1">
            <h2 className="text-2xl font-semibold tracking-tight">Manage your subscription with ease</h2>
            <p className="mt-2 text-muted">Your plan, trial, billing and payments in your MAPIO account, powered by Paddle.</p>
          </div>
          <SubscriptionIllustration />
        </div>
      </section>

      <section aria-labelledby="faq-title" className="mx-auto mt-20 max-w-[900px] px-4">
        <h2 id="faq-title" className="mb-6 text-3xl font-semibold tracking-tight">Frequently asked questions</h2>
        <FaqList items={FAQ_ITEMS.slice(0, 5)} />
        <Link href="/faq" className="mt-4 inline-flex items-center gap-1 font-semibold text-accent-2 hover:underline">
          More questions <ArrowRight className="h-4 w-4" aria-hidden />
        </Link>
      </section>
    </MarketingShell>
  );
}
