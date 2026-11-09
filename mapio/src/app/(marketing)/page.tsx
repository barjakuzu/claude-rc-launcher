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
      <section className="mx-auto grid max-w-[1280px] items-center gap-12 px-4 pb-10 pt-14 lg:grid-cols-[1fr_1.15fr] lg:pt-20">
        <div>
          <p className="chip mb-6">
            <Car className="h-4 w-4 text-accent-2" aria-hidden /> Built for in-car browsers
          </p>
          <h1 className="text-[2.6rem] font-bold leading-[1.05] tracking-tight sm:text-6xl">
            3D navigation for your <span className="text-gradient">Tesla browser</span>
          </h1>
          <p className="mt-6 max-w-xl text-lg leading-relaxed text-muted">
            Clear 3D maps, nearby chargers, send-to-car from your phone, weather and official GEL exchange rates, and
            simple subscription controls — all in MAPIO.
          </p>
          <div className="mt-8 flex flex-wrap gap-3">
            <Link href="/signup" className="btn btn-primary !min-h-14 px-7 text-lg">
              Start {price.trialDays}-day free trial <ArrowRight className="h-5 w-5" aria-hidden />
            </Link>
            <Link href="/how-it-works" className="btn btn-ghost !min-h-14 px-6">
              <PlayCircle className="h-5 w-5" aria-hidden /> See how it works
            </Link>
          </div>
          <p className="mt-4 text-xs text-faint">Independent product · not affiliated with Tesla, Inc.</p>
        </div>
        <ProductPreview />
      </section>

      <section aria-label="Highlights" className="mx-auto max-w-[1280px] px-4">
        <ul className="glass grid grid-cols-2 gap-y-4 px-4 py-5 text-[15px] md:grid-cols-4 md:divide-x md:divide-line">
          {[
            [Car, "Works in in-car browsers"],
            [CalendarDays, `${price.trialDays}-day free trial`],
            [InfinityIcon, "Cancel anytime"],
            [UserRound, "Manage subscription anytime"],
          ].map(([I, t]) => {
            const Icon = I as typeof Car;
            return (
              <li key={t as string} className="flex items-center justify-center gap-3">
                <Icon className="h-6 w-6 text-muted" aria-hidden /> {t as string}
              </li>
            );
          })}
        </ul>
      </section>

      <section id="features" aria-labelledby="features-title" className="mx-auto mt-16 grid max-w-[1280px] gap-6 px-4 lg:grid-cols-[300px_1fr]">
        <div className="lg:pt-6">
          <h2 id="features-title" className="text-3xl font-bold leading-tight">Everything you need for a better trip</h2>
          <p className="mt-3 text-muted">
            A focused navigation experience designed for the big screen in your car — beautiful, readable, and
            subscription-based.
          </p>
          <Link href="/features" className="mt-4 inline-flex items-center gap-1 font-semibold text-accent-2 hover:underline">
            All features <ArrowRight className="h-4 w-4" aria-hidden />
          </Link>
        </div>
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {FEATURES.map((f) => (
            <FeatureTile key={f.title} {...f} />
          ))}
        </div>
      </section>

      <section id="pricing" className="mx-auto mt-16 grid max-w-[1280px] gap-6 px-4 xl:grid-cols-[1.25fr_1fr]">
        <PricingCard price={price} />
        <div className="grid gap-4 md:grid-cols-[1fr_1.4fr] xl:grid-cols-1">
          <div className="px-2">
            <h2 className="text-2xl font-bold">Manage your subscription with ease</h2>
            <p className="mt-2 text-muted">
              Full control of your plan, trial, billing and payments in your MAPIO account — powered by Paddle.
            </p>
          </div>
          <SubscriptionIllustration />
        </div>
      </section>

      <section aria-labelledby="faq-title" className="mx-auto mt-16 max-w-[900px] px-4">
        <h2 id="faq-title" className="mb-6 text-3xl font-bold">Frequently asked questions</h2>
        <FaqList items={FAQ_ITEMS.slice(0, 5)} />
        <Link href="/faq" className="mt-4 inline-flex items-center gap-1 font-semibold text-accent-2 hover:underline">
          More questions <ArrowRight className="h-4 w-4" aria-hidden />
        </Link>
      </section>
    </MarketingShell>
  );
}
