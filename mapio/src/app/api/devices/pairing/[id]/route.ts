import { json, limit, requireApiUser, route } from "@/lib/api";
import { pairingStatus } from "@/lib/repos/devices";

type Ctx = { params: Promise<{ id: string }> };

/** Polled by the car browser while the QR code is shown. */
export const GET = route<Ctx>(async (_req, ctx) => {
  const user = await requireApiUser();
  limit(`pair-poll:${user.id}`, 120, 60_000);
  const { id } = await ctx.params;
  if (!/^[0-9a-f-]{36}$/.test(id)) return json({ status: "not_found" }, 404);
  return json({ status: await pairingStatus(user.id, id) });
});
