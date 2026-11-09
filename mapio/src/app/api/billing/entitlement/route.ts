import { json, requireApiUser, route } from "@/lib/api";
import { getBillingState } from "@/lib/billing";

export const GET = route(async () => {
  const user = await requireApiUser();
  const s = await getBillingState(user.id, user.email);
  return json({ entitlement: s.entitlement, mode: s.mode, environment: s.environment });
});
