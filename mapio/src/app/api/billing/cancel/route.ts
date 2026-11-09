import { HttpError, json, limit, requireApiUser, route } from "@/lib/api";
import { getBillingState, scheduleCancel } from "@/lib/billing";

/** Schedules cancellation at the end of the current trial/billing period (explicit user confirmation in UI). */
export const POST = route(async () => {
  const user = await requireApiUser();
  limit(`cancel:${user.id}`, 5, 60_000);
  try {
    await scheduleCancel(user.id);
  } catch (e) {
    throw new HttpError(409, (e as Error).message);
  }
  return json(await getBillingState(user.id));
});
