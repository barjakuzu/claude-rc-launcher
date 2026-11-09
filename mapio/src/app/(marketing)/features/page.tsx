import type { Metadata } from "next";
import { CheckCircle2, CircleDashed, Clock3 } from "lucide-react";
import { MarketingShell, PageIntro } from "@/components/marketing/shell";
import { FEATURES, FeatureTile } from "@/components/marketing/features";

export const metadata: Metadata = {
  title: "Features",
  description: "What MAPIO does today, what is in beta, and what is not supported — clearly labelled.",
};

const STATUS: { group: string; icon: typeof CheckCircle2; tone: string; items: [string, string][] }[] = [
  {
    group: "Available",
    icon: CheckCircle2,
    tone: "text-ok",
    items: [
      ["3D map with extruded buildings", "Tilted 3D or flat 2D view with building heights from the map provider's data. Buildings without height data use a modest default height — 3D shapes are approximate."],
      ["Search in English and Georgian", "Autocomplete for places and addresses, with recent searches."],
      ["Route preview with turn instructions", "Driving routes and turn-by-turn steps as returned by our routing provider."],
      ["Send to Car", "Pair your car's browser with a QR code and send destinations from your phone."],
      ["Saved places", "Home, Work and favourites synced across browsers."],
      ["Public chargers", "Charger locations, connectors and power from Open Charge Map, with source and last-update time."],
      ["Weather", "Current conditions and today's range for your area, with the observation time."],
      ["Official GEL rates", "National Bank of Georgia reference rates with publication date."],
      ["Light and dark themes, km/mi, °C/°F, 12/24h", "Set in onboarding or your account."],
    ],
  },
  {
    group: "Depends on your browser",
    icon: Clock3,
    tone: "text-warn",
    items: [
      ["Live position & following", "Requires the browser to grant location access. Some in-car browsers provide limited or delayed GPS and heading; when it isn't reliable, MAPIO shows a route preview instead of guidance."],
      ["Background updates", "Browsers may pause tabs in the background, so guidance continues only while MAPIO is on screen."],
      ["Smooth 3D", "Older car computers may render 3D slowly. MAPIO reduces effects automatically and you can switch to 2D."],
    ],
  },
  {
    group: "Not supported",
    icon: CircleDashed,
    tone: "text-faint",
    items: [
      ["Live traffic", "Routes do not account for live traffic."],
      ["Live charger availability / stall counts", "Shown as “availability unknown” unless a verified live source exists."],
      ["Vehicle data or control", "MAPIO does not read battery level, control the car, or send to the car's built-in navigation."],
      ["Lane guidance", "Not provided."],
    ],
  },
];

export default function FeaturesPage() {
  return (
    <MarketingShell>
      <PageIntro eyebrow="Features" title="What MAPIO does — honestly">
        A premium map for the big screen in your car. Here is exactly what works today and what depends on your
        browser.
      </PageIntro>
      <div className="mx-auto max-w-[1100px] px-4">
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {FEATURES.map((f) => (
            <FeatureTile key={f.title} {...f} />
          ))}
        </div>
        {STATUS.map((s) => (
          <section key={s.group} className="mt-12">
            <h2 className={`mb-4 flex items-center gap-2 text-2xl font-bold ${s.tone}`}>
              <s.icon className="h-6 w-6" aria-hidden /> <span className="text-text">{s.group}</span>
            </h2>
            <ul className="grid gap-3 md:grid-cols-2">
              {s.items.map(([h, b]) => (
                <li key={h} className="glass p-5">
                  <h3 className="font-semibold">{h}</h3>
                  <p className="mt-1 text-sm leading-relaxed text-muted">{b}</p>
                </li>
              ))}
            </ul>
          </section>
        ))}
      </div>
    </MarketingShell>
  );
}
