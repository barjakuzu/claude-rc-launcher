import "server-only";
import { integrationSummary } from "@/lib/env";
import { getFxRates } from "@/lib/providers/fx";
import { getWeather } from "@/lib/providers/weather";
import { getChargers } from "@/lib/providers/chargers";
import { searchPlaces } from "@/lib/providers/geo";
import { TBILISI } from "@/lib/providers/fixtures";

type Check = { name: string; mode: string; ok: boolean; ms: number; detail: string };

async function time(name: string, mode: string, fn: () => Promise<string>): Promise<Check> {
  const t = Date.now();
  try {
    const detail = await fn();
    return { name, mode, ok: true, ms: Date.now() - t, detail };
  } catch (e) {
    return { name, mode, ok: false, ms: Date.now() - t, detail: (e as Error).message.slice(0, 160) };
  }
}

/** Live probes of each provider (used by /admin). Counts toward provider quotas — run on demand only. */
export async function providerHealth() {
  const s = integrationSummary();
  const checks = await Promise.all([
    time("Search (Geoapify)", String(s.geocodingRouting), async () => {
      const r = await searchPlaces("Tbilisi", { lang: "en", userId: null, limit: 1 });
      return `${r.places.length} result(s), source ${r.meta.source}`;
    }),
    time("Weather (Open-Meteo)", String(s.weather), async () => {
      const r = await getWeather(TBILISI.lat, TBILISI.lon, null);
      return `${r.weather.tempC}°C observed ${r.weather.observedAt}${r.meta.stale ? " (stale)" : ""}`;
    }),
    time("FX (NBG)", String(s.fx), async () => {
      const r = await getFxRates(null);
      return `${r.fx.rates.length} rates for ${r.fx.publicationDate}${r.meta.stale ? " (stale)" : ""}`;
    }),
    time("Chargers (Open Charge Map)", String(s.chargers), async () => {
      const r = await getChargers(TBILISI.lat, TBILISI.lon, 5, {}, null);
      return `${r.chargers.length} chargers within 5 km`;
    }),
  ]);
  return { summary: s, checks, checkedAt: new Date().toISOString() };
}
