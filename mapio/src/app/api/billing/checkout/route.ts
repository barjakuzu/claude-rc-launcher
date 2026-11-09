import { HttpError, json, limit, requireApiUser, route } from "@/lib/api";
import { startCheckout } from "@/lib/billing";

/** Creates a Paddle transaction server-side (signed custom_data) or points to the dev simulator. */
export const POST = route(async () => {
  const user = await requireApiUser();
  limit(`checkout:${user.id}`, 10, 10 * 60_000);
  const r = await startCheckout(user);
  if (r.kind === "error") throw new HttpError(409, r.message);
  return json(r);
});
