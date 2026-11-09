import crypto from "node:crypto";

/**
 * Paddle Billing webhook signature (header `Paddle-Signature: ts=<unix>;h1=<hex>`).
 * HMAC-SHA256 over `${ts}:${rawBody}` with the notification destination secret.
 * Mirrors @paddle/paddle-node-sdk WebhooksValidator (cross-checked in tests) and
 * additionally accepts several h1 values during secret rotation.
 */
export const DEFAULT_TOLERANCE_S = 5;

export function parseSignatureHeader(header: string): { ts: number; h1: string[] } | null {
  let ts = NaN;
  const h1: string[] = [];
  for (const part of header.split(";")) {
    const idx = part.indexOf("=");
    if (idx < 0) continue;
    const k = part.slice(0, idx).trim();
    const v = part.slice(idx + 1).trim();
    if (k === "ts") ts = Number.parseInt(v, 10);
    if (k === "h1" && v) h1.push(v);
  }
  return Number.isFinite(ts) && h1.length ? { ts, h1 } : null;
}

export function computeSignature(secret: string, ts: number, rawBody: string): string {
  return crypto.createHmac("sha256", secret).update(`${ts}:${rawBody}`).digest("hex");
}

export function signatureHeader(secret: string, rawBody: string, ts = Math.floor(Date.now() / 1000)) {
  return `ts=${ts};h1=${computeSignature(secret, ts, rawBody)}`;
}

export function verifySignature(
  secret: string,
  rawBody: string,
  header: string | null,
  opts: { now?: number; toleranceS?: number } = {},
): boolean {
  if (!header || !secret) return false;
  const parsed = parseSignatureHeader(header);
  if (!parsed) return false;
  const now = opts.now ?? Date.now();
  if (Math.abs(now - parsed.ts * 1000) > (opts.toleranceS ?? DEFAULT_TOLERANCE_S) * 1000) return false;
  const expected = Buffer.from(computeSignature(secret, parsed.ts, rawBody), "hex");
  return parsed.h1.some((h) => {
    const got = Buffer.from(h, "hex");
    return got.length === expected.length && crypto.timingSafeEqual(got, expected);
  });
}
