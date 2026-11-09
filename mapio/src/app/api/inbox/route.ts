import { cookies } from "next/headers";
import { body, HttpError, json, limit, parse, requireApiUser, requirePremium, route } from "@/lib/api";
import { DEVICE_COOKIE, resolveDevice } from "@/lib/repos/devices";
import { fetchDeviceInbox, sendToDevice } from "@/lib/repos/inbox";
import { sendToCarInput } from "@/lib/validation";

/** Car browser poll (and Realtime-triggered refetch): inbox for THIS paired browser. */
export const GET = route(async () => {
  const user = await requireApiUser();
  limit(`inbox-poll:${user.id}`, 120, 60_000);
  const device = await resolveDevice(user.id, (await cookies()).get(DEVICE_COOKIE)?.value);
  if (!device) return json({ paired: false, items: [] });
  return json({ paired: true, deviceId: device.id, deviceName: device.name, items: await fetchDeviceInbox(user.id, device.id) });
});

/** Phone/desktop: send a destination to a paired browser. */
export const POST = route(async (req) => {
  const user = await requirePremium();
  limit(`inbox-send:${user.id}`, 30, 60_000);
  const p = parse(sendToCarInput, await body(req));
  const ua = req.headers.get("user-agent") ?? "";
  const label = /iPhone|Android|Mobile/i.test(ua) ? "Phone" : "Web";
  try {
    return json({ item: await sendToDevice(user.id, p, label) }, 201);
  } catch (e) {
    throw new HttpError(404, (e as Error).message);
  }
});
