import { HttpError, json, limit, requireApiUser, route } from "@/lib/api";
import { getBillingState, undoScheduledCancel } from "@/lib/billing";

export const POST = route(async () => {
  const user = await requireApiUser();
  limit(`undo:${user.id}`, 5, 60_000);
  try {
    await undoScheduledCancel(user.id);
  } catch (e) {
    throw new HttpError(409, (e as Error).message);
  }
  return json(await getBillingState(user.id));
});
