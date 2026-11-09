import { z } from "zod";
import { json, limit, parse, query, requirePremium, route } from "@/lib/api";
import { getChargers } from "@/lib/providers/chargers";
import { lat, lon } from "@/lib/validation";

const schema = z.object({
  lat,
  lon,
  radius: z.coerce.number().min(1).max(50).default(10),
  current: z.enum(["AC", "DC"]).optional(),
  minKw: z.coerce.number().min(0).max(400).optional(),
  connector: z.string().max(40).optional(),
});

export const GET = route(async (req) => {
  const user = await requirePremium();
  limit(`chargers:${user.id}`, 30, 60_000);
  const p = parse(schema, query(req));
  return json(await getChargers(p.lat, p.lon, p.radius, { current: p.current, minKw: p.minKw, connector: p.connector }, user.id));
});
