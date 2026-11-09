import "server-only";
import type { z } from "zod";
import { withUser } from "@/lib/db";
import type { placeInput, savedPlaceInput } from "@/lib/validation";

export interface SavedPlace {
  id: string;
  kind: "home" | "work" | "favorite";
  label: string;
  name: string;
  address: string | null;
  lat: number;
  lon: number;
  source: string | null;
  created_at: string;
}
export interface RecentDestination {
  id: string;
  name: string;
  address: string | null;
  lat: number;
  lon: number;
  used_at: string;
}

export function listSaved(userId: string) {
  return withUser(userId, (tx) =>
    tx.query<SavedPlace>(
      `select id, kind, label, name, address, lat, lon, source, created_at from public.saved_places
       order by case kind when 'home' then 0 when 'work' then 1 else 2 end, created_at desc`,
    ),
  );
}

export async function upsertSaved(userId: string, p: z.infer<typeof savedPlaceInput>) {
  return withUser(userId, async (tx) => {
    if (p.kind !== "favorite") {
      // Home/Work are singletons: replace.
      await tx.query(`delete from public.saved_places where user_id = $1 and kind = $2`, [userId, p.kind]);
    } else {
      const [{ n }] = await tx.query<{ n: number }>(`select count(*)::int as n from public.saved_places`);
      if (n >= 200) throw new Error("Saved places limit reached (200).");
    }
    const [row] = await tx.query<SavedPlace>(
      `insert into public.saved_places (user_id, kind, label, name, address, lat, lon, source, source_ref)
       values ($1,$2,$3,$4,$5,$6,$7,$8,$9) returning *`,
      [userId, p.kind, p.label, p.name, p.address || null, p.lat, p.lon, p.source ?? null, p.sourceRef ?? null],
    );
    return row;
  });
}

export async function renameSaved(userId: string, id: string, label: string) {
  return withUser(userId, (tx) =>
    tx.query(`update public.saved_places set label = $2 where id = $1 returning id`, [id, label]),
  );
}

export async function deleteSaved(userId: string, id: string) {
  return withUser(userId, (tx) => tx.query(`delete from public.saved_places where id = $1 returning id`, [id]));
}

export function listRecent(userId: string, limit = 10) {
  return withUser(userId, (tx) =>
    tx.query<RecentDestination>(
      `select id, name, address, lat, lon, used_at from public.recent_destinations order by used_at desc limit $1`,
      [limit],
    ),
  );
}

export async function addRecent(userId: string, p: z.infer<typeof placeInput>) {
  return withUser(userId, async (tx) => {
    const [pref] = await tx.query<{ keep: boolean }>(
      `select keep_recent_destinations as keep from public.profiles where id = $1`,
      [userId],
    );
    if (pref && !pref.keep) return;
    // de-duplicate by ~10 m and cap at 25 entries
    await tx.query(
      `delete from public.recent_destinations where abs(lat - $1) < 0.0001 and abs(lon - $2) < 0.0001`,
      [p.lat, p.lon],
    );
    await tx.query(
      `insert into public.recent_destinations (user_id, name, address, lat, lon, source) values ($1,$2,$3,$4,$5,$6)`,
      [userId, p.name, p.address || null, p.lat, p.lon, p.source ?? null],
    );
    await tx.query(
      `delete from public.recent_destinations where id in (
         select id from public.recent_destinations order by used_at desc offset 25)`,
    );
  });
}

export function clearRecent(userId: string) {
  return withUser(userId, (tx) => tx.query(`delete from public.recent_destinations`));
}
