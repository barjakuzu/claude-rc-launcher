import { body, json, limit, parse, requirePremium, route } from "@/lib/api";
import { addRecent, clearRecent } from "@/lib/repos/places";
import { placeInput } from "@/lib/validation";

export const POST = route(async (req) => {
  const user = await requirePremium();
  limit(`recent:${user.id}`, 60, 60_000);
  await addRecent(user.id, parse(placeInput, await body(req)));
  return json({ ok: true });
});

export const DELETE = route(async () => {
  const user = await requirePremium();
  await clearRecent(user.id);
  return json({ ok: true });
});
