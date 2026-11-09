import { z } from "zod";
import { json, limit, parse, query, requirePremium, route } from "@/lib/api";
import { searchPlaces } from "@/lib/providers/geo";
import { lang, lat, lon } from "@/lib/validation";

const schema = z.object({ q: z.string().trim().min(2).max(120), lang, lat: lat.optional(), lon: lon.optional() });

export const GET = route(async (req) => {
  const user = await requirePremium();
  limit(`search:${user.id}`, 60, 60_000);
  const p = parse(schema, query(req));
  const near = p.lat != null && p.lon != null ? { lat: p.lat, lon: p.lon } : undefined;
  return json(await searchPlaces(p.q, { lang: p.lang, near, userId: user.id }));
});
