import { z } from "zod";
import { json, limit, parse, query, requirePremium, route } from "@/lib/api";
import { getWeather } from "@/lib/providers/weather";
import { lat, lon } from "@/lib/validation";

export const GET = route(async (req) => {
  const user = await requirePremium();
  limit(`weather:${user.id}`, 30, 60_000);
  const p = parse(z.object({ lat, lon }), query(req));
  return json(await getWeather(p.lat, p.lon, user.id));
});
