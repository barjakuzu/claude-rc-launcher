import { HttpError, json, limit, requireApiUser, route } from "@/lib/api";
import { portalUrl } from "@/lib/billing";

export const POST = route(async () => {
  const user = await requireApiUser();
  limit(`portal:${user.id}`, 10, 60_000);
  const url = await portalUrl(user.id);
  if (!url) throw new HttpError(404, "No subscription to manage yet.");
  return json({ url });
});
