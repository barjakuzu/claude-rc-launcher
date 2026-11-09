import { cookies } from "next/headers";
import { json, requireApiUser, route } from "@/lib/api";
import { DEVICE_COOKIE, listDevices, resolveDevice } from "@/lib/repos/devices";

export const GET = route(async () => {
  const user = await requireApiUser();
  const current = await resolveDevice(user.id, (await cookies()).get(DEVICE_COOKIE)?.value);
  return json({ devices: await listDevices(user.id), currentDeviceId: current?.id ?? null });
});
