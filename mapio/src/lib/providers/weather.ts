import "server-only";
import { z } from "zod";
import { env, weatherMode } from "@/lib/env";
import { fetchJson } from "./http";
import { TtlCache, tileKey } from "./cache";
import { recordUsage } from "./usage";
import { fixtureWeather } from "./fixtures";
import type { ProviderMeta, Weather } from "./types";

/**
 * Open-Meteo adapter. Commercial launches MUST use the customer API with an API key
 * (https://open-meteo.com/en/pricing). The free endpoint is allowed only for local,
 * non-commercial development when OPEN_METEO_ALLOW_FREE_TIER=true.
 */
const cache = new TtlCache<Weather>(2000);
const TTL = 10 * 60_000;

const schema = z.object({
  current: z.object({
    time: z.string(),
    temperature_2m: z.number(),
    weather_code: z.number(),
    wind_speed_10m: z.number().nullable().optional(),
    is_day: z.number(),
  }),
  daily: z
    .object({ temperature_2m_max: z.array(z.number()), temperature_2m_min: z.array(z.number()) })
    .optional(),
});

export function parseOpenMeteo(json: unknown): Weather {
  const d = schema.parse(json);
  return {
    tempC: d.current.temperature_2m,
    weatherCode: d.current.weather_code,
    isDay: d.current.is_day === 1,
    windKmh: d.current.wind_speed_10m ?? null,
    todayMaxC: d.daily?.temperature_2m_max[0] ?? null,
    todayMinC: d.daily?.temperature_2m_min[0] ?? null,
    observedAt: new Date(d.current.time + (d.current.time.endsWith("Z") ? "" : "Z")).toISOString(),
  };
}

export async function getWeather(lat: number, lon: number, userId: string | null): Promise<{ weather: Weather; meta: ProviderMeta }> {
  const mode = weatherMode();
  const attribution = "Weather data by Open-Meteo.com";
  if (mode === "demo") {
    return {
      weather: fixtureWeather(lat),
      meta: { source: "fixture", mode: "demo", fetchedAt: new Date().toISOString(), cached: false, attribution: "MAPIO demo weather (not real)", notice: "Demo weather — not a real observation." },
    };
  }
  const key = tileKey(lat, lon, 0.1);
  const [klat, klon] = key.split(",");
  const base = mode === "live" ? "https://customer-api.open-meteo.com/v1/forecast" : "https://api.open-meteo.com/v1/forecast";
  const url =
    `${base}?latitude=${klat}&longitude=${klon}&timezone=GMT&forecast_days=1` +
    `&current=temperature_2m,weather_code,wind_speed_10m,is_day&daily=temperature_2m_max,temperature_2m_min` +
    (mode === "live" ? `&apikey=${env().OPEN_METEO_API_KEY}` : "");
  try {
    const { value, cached, storedAt } = await cache.get(key, TTL, async () => {
      recordUsage("open_meteo", userId, "request");
      return parseOpenMeteo(await fetchJson(url, { timeoutMs: 5000 }));
    });
    if (cached) recordUsage("open_meteo", userId, "cache_hit");
    return { weather: value, meta: { source: "open-meteo", mode, fetchedAt: new Date(storedAt).toISOString(), cached, attribution } };
  } catch (e) {
    recordUsage("open_meteo", userId, "error");
    const stale = cache.stale(key);
    if (stale) {
      return {
        weather: stale.value,
        meta: { source: "open-meteo", mode, fetchedAt: new Date(stale.storedAt).toISOString(), cached: true, stale: true, attribution, notice: "Weather service unavailable — showing last known data." },
      };
    }
    throw e;
  }
}
