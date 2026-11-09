import "server-only";
import { NextResponse, type NextRequest } from "next/server";
import { z, type ZodType } from "zod";
import { getSessionUser, type SessionUser } from "@/lib/auth";
import { getEntitlement } from "@/lib/billing";
import { ConfigError } from "@/lib/env";
import { ProviderError } from "@/lib/providers/http";
import { rateLimit } from "@/lib/ratelimit";

export class HttpError extends Error {
  constructor(
    public status: number,
    message: string,
    public code?: string,
  ) {
    super(message);
  }
}

export function json(data: unknown, init?: number | ResponseInit) {
  const res = NextResponse.json(data, typeof init === "number" ? { status: init } : init);
  res.headers.set("Cache-Control", "private, no-store");
  return res;
}

/**
 * CSRF defence for cookie-authenticated mutations: the Origin (or Referer) must match
 * the request host. Combined with SameSite=Lax session cookies.
 */
export function assertSameOrigin(req: NextRequest) {
  if (["GET", "HEAD", "OPTIONS"].includes(req.method)) return;
  const origin = req.headers.get("origin") ?? req.headers.get("referer");
  const host = req.headers.get("x-forwarded-host") ?? req.headers.get("host");
  if (!origin || !host) throw new HttpError(403, "Missing origin");
  let o: URL;
  try {
    o = new URL(origin);
  } catch {
    throw new HttpError(403, "Bad origin");
  }
  if (o.host !== host) throw new HttpError(403, "Cross-origin request blocked");
}

export async function requireApiUser(): Promise<SessionUser> {
  const u = await getSessionUser();
  if (!u) throw new HttpError(401, "Sign in required", "auth_required");
  return u;
}

export async function requirePremium(): Promise<SessionUser> {
  const u = await requireApiUser();
  const e = await getEntitlement(u.id);
  if (!e.hasAccess) throw new HttpError(402, "An active MAPIO Premium subscription or trial is required.", "subscription_required");
  return u;
}

export function limit(key: string, max: number, windowMs: number) {
  const r = rateLimit(key, max, windowMs);
  if (!r.ok) throw new HttpError(429, `Too many requests. Retry in ${r.retryAfterS}s.`, "rate_limited");
}

export function parse<T>(schema: ZodType<T>, data: unknown): T {
  const r = schema.safeParse(data);
  if (!r.success) throw new HttpError(400, r.error.issues.map((i) => `${i.path.join(".") || "input"}: ${i.message}`).join("; "), "invalid_input");
  return r.data;
}

export function query(req: NextRequest) {
  return Object.fromEntries(req.nextUrl.searchParams.entries());
}

export async function body(req: NextRequest): Promise<unknown> {
  const len = Number(req.headers.get("content-length") ?? 0);
  if (len > 32_000) throw new HttpError(413, "Body too large");
  try {
    return await req.json();
  } catch {
    throw new HttpError(400, "Invalid JSON body");
  }
}

type Handler<C> = (req: NextRequest, ctx: C) => Promise<Response>;

/** Wraps a route handler with origin checks and consistent, non-leaky error responses. */
export function route<C = unknown>(fn: Handler<C>): Handler<C> {
  return async (req, ctx) => {
    try {
      assertSameOrigin(req);
      return await fn(req, ctx);
    } catch (e) {
      if (e instanceof HttpError) return json({ error: e.message, code: e.code }, e.status);
      if (e instanceof ConfigError) return json({ error: "This feature is not configured on the server.", code: "not_configured" }, 503);
      if (e instanceof ProviderError) {
        console.error("[provider]", e.message);
        return json({ error: "A data provider is temporarily unavailable. Please try again.", code: "provider_unavailable" }, 502);
      }
      if (e instanceof z.ZodError) return json({ error: "Unexpected provider response.", code: "provider_format" }, 502);
      console.error("[api]", e);
      return json({ error: "Internal error", code: "internal" }, 500);
    }
  };
}
