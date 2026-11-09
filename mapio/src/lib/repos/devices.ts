import "server-only";
import { withService, withUser } from "@/lib/db";
import { randomToken, sha256, timingSafeEqualStr } from "@/lib/auth/crypto";

export const PAIRING_TTL_MS = 5 * 60_000;
export const DEVICE_COOKIE = "mapio_device";
export const PAIR_NONCE_COOKIE = "mapio_pair_nonce";

export interface PairedDevice {
  id: string;
  name: string;
  device_type: "car_browser" | "phone" | "desktop" | "other";
  user_agent: string | null;
  created_at: string;
  last_seen_at: string | null;
  revoked_at: string | null;
}

export type PairingStatus = "pending" | "approved" | "redeemed" | "expired" | "not_found";

/** Step 1 (car browser, signed in): create a one-time pairing request. */
export async function createPairingRequest(userId: string, name: string, userAgent: string | null) {
  const token = randomToken(24);
  const nonce = randomToken(24);
  const expiresAt = new Date(Date.now() + PAIRING_TTL_MS);
  const [row] = await withUser(userId, async (tx) => {
    // keep the table small: drop this user's stale, unredeemed requests
    await tx.query(
      `delete from public.device_pairing_requests where redeemed_at is null and expires_at < now() - interval '1 hour'`,
    );
    const [{ n }] = await tx.query<{ n: number }>(
      `select count(*)::int as n from public.device_pairing_requests
       where redeemed_at is null and expires_at > now()`,
    );
    if (n >= 5) throw new PairingError("Too many open pairing codes. Wait a few minutes and try again.");
    return tx.query<{ id: string }>(
      `insert into public.device_pairing_requests (user_id, token_hash, browser_nonce_hash, device_name, user_agent, expires_at)
       values ($1,$2,$3,$4,$5,$6) returning id`,
      [userId, sha256(token), sha256(nonce), name, userAgent?.slice(0, 300) ?? null, expiresAt.toISOString()],
    );
  });
  return { requestId: row.id, token, nonce, expiresAt: expiresAt.toISOString() };
}

export class PairingError extends Error {}

/** Step 2 (phone, signed in to the SAME account): approve via the scanned token. */
export async function approvePairing(userId: string, token: string) {
  return withService(async (tx) => {
    const [req] = await tx.query<{
      id: string;
      user_id: string;
      device_name: string;
      expires_at: Date;
      approved_at: Date | null;
      redeemed_at: Date | null;
    }>(
      `select id, user_id, device_name, expires_at, approved_at, redeemed_at
       from public.device_pairing_requests where token_hash = $1 for update`,
      [sha256(token)],
    );
    if (!req) throw new PairingError("This pairing code is invalid.");
    if (req.user_id !== userId) throw new PairingError("This pairing code belongs to a different MAPIO account.");
    if (req.redeemed_at || req.approved_at) throw new PairingError("This pairing code has already been used.");
    if (new Date(req.expires_at).getTime() < Date.now()) throw new PairingError("This pairing code has expired.");
    await tx.query(
      `update public.device_pairing_requests set approved_at = now(), approved_by = $2 where id = $1`,
      [req.id, userId],
    );
    return { deviceName: req.device_name };
  });
}

export async function peekPairingToken(userId: string, token: string) {
  const rows = await withService((tx) =>
    tx.query<{ user_id: string; device_name: string; expires_at: Date; approved_at: Date | null }>(
      `select user_id, device_name, expires_at, approved_at from public.device_pairing_requests where token_hash = $1`,
      [sha256(token)],
    ),
  );
  const r = rows[0];
  if (!r) return { state: "invalid" as const };
  if (r.user_id !== userId) return { state: "other_account" as const };
  if (r.approved_at) return { state: "used" as const, deviceName: r.device_name };
  if (new Date(r.expires_at).getTime() < Date.now()) return { state: "expired" as const, deviceName: r.device_name };
  return { state: "ok" as const, deviceName: r.device_name };
}

export async function pairingStatus(userId: string, requestId: string): Promise<PairingStatus> {
  const [r] = await withUser(userId, (tx) =>
    tx.query<{ expires_at: Date; approved_at: Date | null; redeemed_at: Date | null }>(
      `select expires_at, approved_at, redeemed_at from public.device_pairing_requests where id = $1`,
      [requestId],
    ),
  );
  if (!r) return "not_found";
  if (r.redeemed_at) return "redeemed";
  if (new Date(r.expires_at).getTime() < Date.now()) return "expired";
  return r.approved_at ? "approved" : "pending";
}

/** Step 3 (car browser): exchange an approved request for a device credential. One use only. */
export async function redeemPairing(userId: string, requestId: string, nonce: string | undefined) {
  if (!nonce) throw new PairingError("Pairing must be completed in the browser that created the code.");
  return withUser(userId, async (tx) => {
    const [req] = await tx.query<{
      id: string;
      device_name: string;
      user_agent: string | null;
      browser_nonce_hash: string;
      expires_at: Date;
      approved_at: Date | null;
      redeemed_at: Date | null;
    }>(`select * from public.device_pairing_requests where id = $1 for update`, [requestId]);
    if (!req) throw new PairingError("Pairing request not found.");
    if (!timingSafeEqualStr(req.browser_nonce_hash, sha256(nonce))) {
      throw new PairingError("Pairing must be completed in the browser that created the code.");
    }
    if (req.redeemed_at) throw new PairingError("This pairing code has already been used.");
    if (!req.approved_at) throw new PairingError("Pairing has not been approved yet.");
    if (new Date(req.expires_at).getTime() < Date.now()) throw new PairingError("This pairing code has expired.");
    const secret = randomToken(32);
    const [dev] = await tx.query<{ id: string }>(
      `insert into public.paired_devices (user_id, name, device_type, secret_hash, user_agent, last_seen_at)
       values ($1, $2, 'car_browser', $3, $4, now()) returning id`,
      [userId, req.device_name, sha256(secret), req.user_agent],
    );
    const upd = await tx.query(
      `update public.device_pairing_requests set redeemed_at = now(), paired_device_id = $2
       where id = $1 and redeemed_at is null returning id`,
      [req.id, dev.id],
    );
    if (!upd.length) throw new PairingError("This pairing code has already been used.");
    return { deviceId: dev.id, credential: `${dev.id}.${secret}` };
  });
}

/** Resolves the device credential cookie for the signed-in user; null if absent/invalid/revoked. */
export async function resolveDevice(userId: string, credential: string | undefined): Promise<PairedDevice | null> {
  if (!credential) return null;
  const [id, secret] = credential.split(".");
  if (!id || !secret || !/^[0-9a-f-]{36}$/.test(id)) return null;
  const [d] = await withUser(userId, (tx) =>
    tx.query<PairedDevice & { secret_hash: string }>(`select * from public.paired_devices where id = $1`, [id]),
  );
  if (!d || d.revoked_at || !timingSafeEqualStr(d.secret_hash, sha256(secret))) return null;
  // throttle last_seen writes to once a minute
  if (!d.last_seen_at || Date.now() - new Date(d.last_seen_at).getTime() > 60_000) {
    await withUser(userId, (tx) => tx.query(`update public.paired_devices set last_seen_at = now() where id = $1`, [id]));
  }
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  const { secret_hash, ...pub } = d;
  return pub;
}

export function listDevices(userId: string) {
  return withUser(userId, (tx) =>
    tx.query<PairedDevice>(
      `select id, name, device_type, user_agent, created_at, last_seen_at, revoked_at
       from public.paired_devices order by revoked_at nulls first, created_at desc`,
    ),
  );
}

export function renameDevice(userId: string, id: string, name: string) {
  return withUser(userId, (tx) =>
    tx.query(`update public.paired_devices set name = $2 where id = $1 and revoked_at is null returning id`, [id, name]),
  );
}

export function revokeDevice(userId: string, id: string) {
  return withUser(userId, (tx) =>
    tx.query(`update public.paired_devices set revoked_at = now() where id = $1 and revoked_at is null returning id`, [id]),
  );
}
