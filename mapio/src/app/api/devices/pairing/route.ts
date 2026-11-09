import { cookies } from "next/headers";
import { z } from "zod";
import { body, json, limit, parse, requirePremium, route, HttpError } from "@/lib/api";
import { createPairingRequest, PairingError, PAIR_NONCE_COOKIE, PAIRING_TTL_MS } from "@/lib/repos/devices";
import { deviceName } from "@/lib/validation";
import { env, isProduction } from "@/lib/env";

/** Step 1 — called by the car browser. Returns the one-time QR URL; nonce goes in an httpOnly cookie. */
export const POST = route(async (req) => {
  const user = await requirePremium();
  limit(`pair-create:${user.id}`, 10, 10 * 60_000);
  const p = parse(z.object({ name: deviceName.default("Car browser") }), await body(req));
  try {
    const r = await createPairingRequest(user.id, p.name, req.headers.get("user-agent"));
    (await cookies()).set(`${PAIR_NONCE_COOKIE}_${r.requestId}`, r.nonce, {
      httpOnly: true,
      sameSite: "lax",
      secure: isProduction(),
      path: "/api/devices/pairing",
      maxAge: Math.ceil(PAIRING_TTL_MS / 1000) + 120,
    });
    const origin = req.nextUrl.origin || env().APP_URL;
    return json({ requestId: r.requestId, approveUrl: `${origin}/pair/${r.token}`, expiresAt: r.expiresAt });
  } catch (e) {
    if (e instanceof PairingError) throw new HttpError(409, e.message);
    throw e;
  }
});
