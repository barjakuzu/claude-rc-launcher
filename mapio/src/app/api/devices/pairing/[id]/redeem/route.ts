import { cookies } from "next/headers";
import { HttpError, json, limit, requireApiUser, route } from "@/lib/api";
import { DEVICE_COOKIE, PAIR_NONCE_COOKIE, PairingError, redeemPairing } from "@/lib/repos/devices";
import { isProduction } from "@/lib/env";

type Ctx = { params: Promise<{ id: string }> };

/** Step 3 — the car browser exchanges its approved request for a long-lived device credential. */
export const POST = route<Ctx>(async (_req, ctx) => {
  const user = await requireApiUser();
  limit(`pair-redeem:${user.id}`, 10, 60_000);
  const { id } = await ctx.params;
  if (!/^[0-9a-f-]{36}$/.test(id)) throw new HttpError(404, "Not found");
  const jar = await cookies();
  const nonceName = `${PAIR_NONCE_COOKIE}_${id}`;
  try {
    const r = await redeemPairing(user.id, id, jar.get(nonceName)?.value);
    jar.delete({ name: nonceName, path: "/api/devices/pairing" });
    jar.set(DEVICE_COOKIE, r.credential, {
      httpOnly: true,
      sameSite: "lax",
      secure: isProduction(),
      path: "/",
      maxAge: 60 * 60 * 24 * 365,
    });
    return json({ deviceId: r.deviceId });
  } catch (e) {
    if (e instanceof PairingError) throw new HttpError(409, e.message);
    throw e;
  }
});
