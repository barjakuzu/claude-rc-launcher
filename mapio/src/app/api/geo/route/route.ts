import { z } from "zod";
import { body, json, limit, parse, requirePremium, route } from "@/lib/api";
import { routeDriving } from "@/lib/providers/geo";
import { lang, lat, lon } from "@/lib/validation";

const schema = z.object({
  from: z.object({ lat, lon }),
  to: z.object({ lat, lon }),
  lang: lang.optional().default("en"),
});

export const POST = route(async (req) => {
  const user = await requirePremium();
  limit(`route:${user.id}`, 20, 60_000);
  const p = parse(schema, await body(req));
  return json(await routeDriving([p.from.lon, p.from.lat], [p.to.lon, p.to.lat], { lang: p.lang, userId: user.id }));
});
