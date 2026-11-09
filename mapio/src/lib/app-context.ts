import "server-only";
import { cache } from "react";
import { cookies } from "next/headers";
import { requireUser } from "@/lib/auth";
import { getProfile } from "@/lib/repos/profiles";
import { getBillingState } from "@/lib/billing";
import { dict } from "@/lib/i18n";
import { getFlags } from "@/lib/repos/admin";
import { DEVICE_COOKIE, listDevices, resolveDevice } from "@/lib/repos/devices";
import { unreadCount } from "@/lib/repos/inbox";
import type { Units } from "@/lib/format";
import { mapClientConfig } from "@/lib/map/config";
import { TBILISI } from "@/lib/providers/fixtures";
import type { LngLat } from "@/lib/providers/types";

/** Per-request context for signed-in pages (deduplicated with React cache). */
export const getAppContext = cache(async () => {
  const user = await requireUser();
  const [profile, billing, flags] = await Promise.all([getProfile(user.id), getBillingState(user.id, user.email), getFlags()]);
  const units: Units = { distance: profile.distance_unit, temperature: profile.temperature_unit, time: profile.time_format };
  return {
    user,
    profile,
    billing,
    entitlement: billing.entitlement,
    t: dict(profile.locale),
    units,
    flags: {
      chargers: flags.chargers ?? true,
      weather: flags.weather ?? true,
      fx_rates: flags.fx_rates ?? true,
      send_to_car: flags.send_to_car ?? true,
    },
    isAdmin: profile.role === "admin",
  };
});

export async function getMapPageData(userId: string) {
  const [devices, current] = await Promise.all([
    listDevices(userId),
    resolveDevice(userId, (await cookies()).get(DEVICE_COOKIE)?.value),
  ]);
  return {
    mapConfig: mapClientConfig(),
    devices: devices.filter((d) => !d.revoked_at).map((d) => ({ id: d.id, name: d.name })),
    currentDeviceId: current?.id ?? null,
    initialCenter: [TBILISI.lon, TBILISI.lat] as LngLat,
  };
}

export async function getUnread(userId: string) {
  try {
    return await unreadCount(userId);
  } catch {
    return 0;
  }
}
