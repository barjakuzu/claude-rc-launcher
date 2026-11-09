import { z } from "zod";
import { json, limit, parse, query, requirePremium, route } from "@/lib/api";
import { reverseGeocode } from "@/lib/providers/geo";
import { lang, lat, lon } from "@/lib/validation";

export const GET = route(async (req) => {
  const user = await requirePremium();
  limit(`reverse:${user.id}`, 30, 60_000);
  const p = parse(z.object({ lat, lon, lang }), query(req));
  return json(await reverseGeocode(p.lat, p.lon, { lang: p.lang, userId: user.id }));
});
