import "server-only";
import { withUser } from "@/lib/db";
import type { z } from "zod";
import type { profileInput } from "@/lib/validation";

export interface Profile {
  id: string;
  display_name: string | null;
  locale: "en" | "ka";
  distance_unit: "km" | "mi";
  temperature_unit: "c" | "f";
  time_format: "24h" | "12h";
  currency: string;
  timezone: string;
  theme: "dark" | "light" | "system";
  notify_inbox: boolean;
  keep_recent_destinations: boolean;
  role: "user" | "admin";
  onboarded_at: string | null;
  created_at: string;
}

export async function getProfile(userId: string): Promise<Profile> {
  return withUser(userId, async (tx) => {
    let [p] = await tx.query<Profile>(`select * from public.profiles where id = $1`, [userId]);
    if (!p) {
      // Trigger normally creates it; self-heal for users created before the trigger existed.
      [p] = await tx.query<Profile>(`insert into public.profiles (id) values ($1) returning *`, [userId]);
    }
    return p;
  });
}

export async function updateProfile(userId: string, input: z.infer<typeof profileInput>, markOnboarded = false) {
  await withUser(userId, (tx) =>
    tx.query(
      `update public.profiles set display_name = $2, locale = $3, distance_unit = $4, temperature_unit = $5,
         time_format = $6, currency = $7, theme = $8, notify_inbox = $9, keep_recent_destinations = $10,
         onboarded_at = case when $11 then coalesce(onboarded_at, now()) else onboarded_at end
       where id = $1`,
      [
        userId,
        input.display_name || null,
        input.locale,
        input.distance_unit,
        input.temperature_unit,
        input.time_format,
        input.currency,
        input.theme,
        input.notify_inbox,
        input.keep_recent_destinations,
        markOnboarded,
      ],
    ),
  );
  if (!input.keep_recent_destinations) {
    await withUser(userId, (tx) => tx.query(`delete from public.recent_destinations`));
  }
}

/** GDPR-style export of everything MAPIO stores for the user (no provider secrets). */
export async function exportUserData(userId: string) {
  return withUser(userId, async (tx) => ({
    exportedAt: new Date().toISOString(),
    profile: (await tx.query(`select * from public.profiles`))[0] ?? null,
    savedPlaces: await tx.query(`select * from public.saved_places order by created_at`),
    recentDestinations: await tx.query(`select * from public.recent_destinations order by used_at desc`),
    devices: await tx.query(
      `select id, name, device_type, created_at, last_seen_at, revoked_at from public.paired_devices order by created_at`,
    ),
    inbox: await tx.query(`select * from public.inbox_destinations order by sent_at desc`),
    subscriptions: await tx.query(
      `select provider, status, currency_code, unit_price_amount, trial_start, trial_end, current_period_start,
              current_period_end, next_billed_at, scheduled_change_action, canceled_at, created_at
       from public.subscriptions`,
    ),
  }));
}
