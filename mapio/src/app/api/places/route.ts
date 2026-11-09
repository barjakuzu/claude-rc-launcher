import { body, json, limit, parse, requireApiUser, requirePremium, route } from "@/lib/api";
import { listRecent, listSaved, upsertSaved } from "@/lib/repos/places";
import { savedPlaceInput } from "@/lib/validation";

export const GET = route(async () => {
  const user = await requireApiUser();
  const [saved, recent] = await Promise.all([listSaved(user.id), listRecent(user.id)]);
  return json({ saved, recent });
});

export const POST = route(async (req) => {
  const user = await requirePremium();
  limit(`places:${user.id}`, 60, 60_000);
  const p = parse(savedPlaceInput, await body(req));
  return json({ place: await upsertSaved(user.id, p) }, 201);
});
