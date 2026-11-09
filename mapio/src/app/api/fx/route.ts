import { json, limit, requirePremium, route } from "@/lib/api";
import { getFxRates } from "@/lib/providers/fx";

export const GET = route(async () => {
  const user = await requirePremium();
  limit(`fx:${user.id}`, 30, 60_000);
  return json(await getFxRates(user.id));
});
