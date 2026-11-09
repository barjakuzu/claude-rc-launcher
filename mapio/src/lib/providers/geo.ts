import "server-only";
import { z } from "zod";
import { env, geoMode } from "@/lib/env";
import { fetchJson } from "./http";
import { TtlCache } from "./cache";
import { recordUsage } from "./usage";
import { fixtureReverse, fixtureRoute, fixtureSearch } from "./fixtures";
import type { LngLat, Place, ProviderMeta, Route, RouteStep } from "./types";

/**
 * Geocoding + routing provider abstraction. Live adapter: Geoapify
 * (https://apidocs.geoapify.com/docs/routing/api/). Demo adapter: fixtures.
 * Verify current response formats and commercial terms before launch (docs/providers.md).
 */
const GEOAPIFY = "https://api.geoapify.com/v1";
const ATTR_GEOAPIFY = "Powered by Geoapify · © OpenStreetMap contributors";
const ATTR_DEMO = "MAPIO demo data (not real search/routing)";

const meta = (mode: "live" | "demo", cached: boolean, notice?: string): ProviderMeta => ({
  source: mode === "live" ? "geoapify" : "fixture",
  mode,
  fetchedAt: new Date().toISOString(),
  cached,
  attribution: mode === "live" ? ATTR_GEOAPIFY : ATTR_DEMO,
  notice,
});

const searchCache = new TtlCache<Place[]>(1000);

const autocompleteSchema = z.object({
  results: z
    .array(
      z.object({
        place_id: z.string().optional(),
        name: z.string().optional(),
        formatted: z.string().optional(),
        address_line1: z.string().optional(),
        address_line2: z.string().optional(),
        lat: z.number(),
        lon: z.number(),
        result_type: z.string().optional(),
        category: z.string().optional(),
      }),
    )
    .default([]),
});

export async function searchPlaces(
  q: string,
  opts: { lang: "en" | "ka"; near?: { lat: number; lon: number }; userId: string | null; limit?: number },
): Promise<{ places: Place[]; meta: ProviderMeta }> {
  const limit = Math.min(opts.limit ?? 8, 10);
  if (geoMode() === "demo") {
    return { places: fixtureSearch(q, opts.lang, limit), meta: meta("demo", false, "Demo search over a few Georgian landmarks.") };
  }
  const near = opts.near ? `${opts.near.lon.toFixed(3)},${opts.near.lat.toFixed(3)}` : "44.827,41.715";
  const key = `${opts.lang}|${near}|${q.toLowerCase()}`;
  const { value, cached } = await searchCache.get(key, 10 * 60_000, async () => {
    recordUsage("geoapify_autocomplete", opts.userId, "request");
    const url =
      `${GEOAPIFY}/geocode/autocomplete?format=json&limit=${limit}&lang=${opts.lang}` +
      `&bias=proximity:${near}&text=${encodeURIComponent(q)}&apiKey=${env().GEOAPIFY_API_KEY}`;
    const json = autocompleteSchema.parse(await fetchJson(url, { timeoutMs: 5000 }));
    return json.results.map((r, i) => ({
      id: r.place_id ?? `geo_${i}_${r.lat}_${r.lon}`,
      name: r.name ?? r.address_line1 ?? r.formatted ?? "Unnamed place",
      address: r.address_line2 ?? r.formatted ?? "",
      lat: r.lat,
      lon: r.lon,
      category: r.category ?? r.result_type ?? null,
      source: "geoapify",
    }));
  });
  if (cached) recordUsage("geoapify_autocomplete", opts.userId, "cache_hit");
  return { places: value, meta: meta("live", cached) };
}

export async function reverseGeocode(
  lat: number,
  lon: number,
  opts: { lang: "en" | "ka"; userId: string | null },
): Promise<{ place: Place; meta: ProviderMeta }> {
  if (geoMode() === "demo") return { place: fixtureReverse(lat, lon), meta: meta("demo", false) };
  recordUsage("geoapify_reverse", opts.userId, "request");
  const url = `${GEOAPIFY}/geocode/reverse?format=json&lang=${opts.lang}&lat=${lat}&lon=${lon}&apiKey=${env().GEOAPIFY_API_KEY}`;
  const json = autocompleteSchema.parse(await fetchJson(url, { timeoutMs: 5000 }));
  const r = json.results[0];
  return {
    place: {
      id: r?.place_id ?? `pin_${lat}_${lon}`,
      name: r?.name ?? r?.address_line1 ?? "Dropped pin",
      address: r?.address_line2 ?? r?.formatted ?? `${lat.toFixed(5)}, ${lon.toFixed(5)}`,
      lat,
      lon,
      category: r?.category ?? null,
      source: "geoapify",
    },
    meta: meta("live", false),
  };
}

const routingSchema = z.object({
  features: z
    .array(
      z.object({
        geometry: z.object({
          type: z.string(),
          coordinates: z.array(z.array(z.array(z.number()))),
        }),
        properties: z.object({
          distance: z.number(),
          time: z.number(),
          legs: z
            .array(
              z.object({
                steps: z
                  .array(
                    z.object({
                      from_index: z.number(),
                      distance: z.number(),
                      time: z.number(),
                      instruction: z.object({ text: z.string(), type: z.string().optional() }).optional(),
                    }),
                  )
                  .default([]),
              }),
            )
            .default([]),
        }),
      }),
    )
    .min(1),
});

/** Pure parser — unit tested against a recorded-shape fixture. */
export function parseGeoapifyRoute(json: unknown): Route {
  const f = routingSchema.parse(json).features[0];
  const lines = f.geometry.coordinates as number[][][];
  const geometry: LngLat[] = lines.flat().map((c) => [c[0], c[1]] as LngLat);
  const steps: RouteStep[] = [];
  f.properties.legs.forEach((leg, li) => {
    for (const s of leg.steps) {
      const c = lines[li]?.[s.from_index] ?? lines[li]?.[0] ?? geometry[0];
      steps.push({
        instruction: s.instruction?.text ?? "",
        distanceM: s.distance,
        durationS: s.time,
        maneuver: s.instruction?.type ?? null,
        location: [c[0], c[1]],
      });
    }
  });
  return { geometry, distanceM: f.properties.distance, durationS: f.properties.time, steps, trafficAware: false };
}

export async function routeDriving(
  from: LngLat,
  to: LngLat,
  opts: { lang: "en" | "ka"; userId: string | null },
): Promise<{ route: Route; meta: ProviderMeta }> {
  if (geoMode() === "demo") {
    return {
      route: fixtureRoute(from, to),
      meta: meta("demo", false, "Demo route: a straight-line approximation, not following roads. Do not drive by it."),
    };
  }
  recordUsage("geoapify_routing", opts.userId, "request");
  const wp = `${from[1]},${from[0]}|${to[1]},${to[0]}`;
  const url =
    `${GEOAPIFY}/routing?waypoints=${encodeURIComponent(wp)}&mode=drive&lang=${opts.lang}` +
    `&details=instruction_details&apiKey=${env().GEOAPIFY_API_KEY}`;
  const route = parseGeoapifyRoute(await fetchJson(url, { timeoutMs: 10_000 }));
  return { route, meta: meta("live", false, "Route without live traffic. Always follow road signs.") };
}
