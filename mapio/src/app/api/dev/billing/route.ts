import { z } from "zod";
import { body, HttpError, json, parse, requireApiUser, route } from "@/lib/api";
import { devSimulate, getBillingState } from "@/lib/billing";
import { billingMode } from "@/lib/env";

/** DEV ONLY billing simulator — refuses to run unless billing is in demo mode. */
export const POST = route(async (req) => {
  const user = await requireApiUser();
  if (billingMode() !== "demo") throw new HttpError(404, "Not found");
  const p = parse(
    z.object({
      action: z.enum(["start_trial", "convert_trial", "schedule_cancel", "undo_cancel", "cancel_now", "fail_payment", "recover_payment", "expire_trial_unpaid"]),
    }),
    await body(req),
  );
  try {
    const outcome = await devSimulate(user.id, p.action);
    return json({ outcome, state: await getBillingState(user.id, user.email) });
  } catch (e) {
    throw new HttpError(409, (e as Error).message);
  }
});
