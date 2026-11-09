import "server-only";
import { z } from "zod";
import { chargersMode, env } from "@/lib/env";
import { fetchJson } from "./http";
import { TtlCache, tileKey } from "./cache";
import { recordUsage } from "./usage";
import { fixtureChargers, haversineM } from "./fixtures";
import type { Charger, CurrentKind, ProviderMeta } from "./types";

/**
 * Open Charge Map adapter (https://www.openchargemap.org/develop/api).
 * OCM provides reported POI data and an operational status — NOT live stall occupancy.
 * MAPIO therefore always reports liveAvailability: "unknown".
 */
const OCM = "https://api.openchargemap.io/v3/poi";
const cache = new TtlCache<Charger[]>(500);
const TTL = 30 * 60_000;

const ocmSchema = z.array(
  z.object({
    ID: z.number(),
    AddressInfo: z.object({
      Title: z.string().nullish(),
      AddressLine1: z.string().nullish(),
      Town: z.string().nullish(),
      Latitude: z.number(),
      Longitude: z.number(),
      Distance: z.number().nullish(),
    }),
    OperatorInfo: z.object({ Title: z.string().nullish() }).nullish(),
    StatusType: z.object({ Title: z.string().nullish(), IsOperational: z.boolean().nullish() }).nullish(),
    DateLastStatusUpdate: z.string().nullish(),
    DataProvider: z
      .object({ Title: z.string().nullish(), License: z.string().nullish() })
      .nullish(),
    Connections: z
      .array(
        z.object({
          ConnectionType: z.object({ Title: z.string().nullish() }).nullish(),
          CurrentType: z.object({ Title: z.string().nullish() }).nullish(),
          PowerKW: z.number().nullish(),
          Quantity: z.number().nullish(),
        }),
      )
      .nullish(),
  }),
);

function currentKind(title: string | null | undefined): CurrentKind {
  if (!title) return "unknown";
  if (/DC/i.test(title)) return "DC";
  if (/AC/i.test(title)) return "AC";
  return "unknown";
}

export function parseOcm(json: unknown, origin: { lat: number; lon: number }): Charger[] {
  return ocmSchema.parse(json).map((p) => {
    const connectors = (p.Connections ?? []).map((c) => ({
      type: c.ConnectionType?.Title ?? "Unknown",
      powerKw: c.PowerKW ?? null,
      current: currentKind(c.CurrentType?.Title),
      quantity: c.Quantity ?? null,
    }));
    const powers = connectors.map((c) => c.powerKw).filter((v): v is number => v != null);
    return {
      id: `ocm_${p.ID}`,
      title: p.AddressInfo.Title ?? "Charging location",
      address: [p.AddressInfo.AddressLine1, p.AddressInfo.Town].filter(Boolean).join(", "),
      lat: p.AddressInfo.Latitude,
      lon: p.AddressInfo.Longitude,
      operator: p.OperatorInfo?.Title ?? null,
      connectors,
      maxPowerKw: powers.length ? Math.max(...powers) : null,
      reportedStatus: p.StatusType?.Title ?? null,
      statusUpdatedAt: p.DateLastStatusUpdate ?? null,
      liveAvailability: "unknown" as const,
      dataProvider: p.DataProvider?.Title ?? "Open Charge Map",
      license: p.DataProvider?.License ?? null,
      distanceKm: haversineM([origin.lon, origin.lat], [p.AddressInfo.Longitude, p.AddressInfo.Latitude]) / 1000,
    };
  });
}

export interface ChargerFilter {
  current?: "AC" | "DC";
  minKw?: number;
  connector?: string;
}

export function filterChargers(list: Charger[], f: ChargerFilter): Charger[] {
  return list.filter((c) => {
    if (f.current && !c.connectors.some((k) => k.current === f.current)) return false;
    if (f.minKw && !(c.maxPowerKw != null && c.maxPowerKw >= f.minKw)) return false;
    if (f.connector && !c.connectors.some((k) => k.type.toLowerCase().includes(f.connector!.toLowerCase()))) return false;
    return true;
  });
}

export async function getChargers(
  lat: number,
  lon: number,
  radiusKm: number,
  filter: ChargerFilter,
  userId: string | null,
): Promise<{ chargers: Charger[]; meta: ProviderMeta }> {
  if (chargersMode() === "demo") {
    return {
      chargers: filterChargers(fixtureChargers(lat, lon), filter),
      meta: { source: "fixture", mode: "demo", fetchedAt: new Date().toISOString(), cached: false, attribution: "MAPIO demo chargers (synthetic)", notice: "Demo chargers are synthetic and do not exist." },
    };
  }
  const key = `${tileKey(lat, lon, 0.05)}|${radiusKm}`;
  const [klat, klon] = tileKey(lat, lon, 0.05).split(",").map(Number);
  const { value, cached, storedAt } = await cache.get(key, TTL, async () => {
    recordUsage("open_charge_map", userId, "request");
    const url =
      `${OCM}?output=json&compact=false&verbose=false&maxresults=100&distanceunit=KM` +
      `&latitude=${klat}&longitude=${klon}&distance=${radiusKm}`;
    return parseOcm(await fetchJson(url, { headers: { "X-API-Key": env().OPEN_CHARGE_MAP_API_KEY! }, timeoutMs: 8000 }), { lat, lon });
  });
  if (cached) recordUsage("open_charge_map", userId, "cache_hit");
  return {
    chargers: filterChargers(value, filter).sort((a, b) => (a.distanceKm ?? 0) - (b.distanceKm ?? 0)),
    meta: {
      source: "open-charge-map",
      mode: "live",
      fetchedAt: new Date(storedAt).toISOString(),
      cached,
      attribution: "Charging data © Open Charge Map contributors (see per-site data provider & licence)",
      notice: "Live stall availability is not available from this source.",
    },
  };
}
