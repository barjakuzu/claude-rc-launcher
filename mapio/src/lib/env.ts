import "server-only";
import { z } from "zod";

/**
 * Central server configuration. Every provider is resolved to either a LIVE adapter
 * (credentials present) or an explicit DEMO adapter (fixtures / local simulators).
 * Demo adapters are refused in production unless MAPIO_DEMO_MODE=true, so a
 * misconfigured deploy fails loudly instead of silently serving fake data.
 */
const schema = z.object({
  NODE_ENV: z.string().default("development"),
  MAPIO_DEMO_MODE: z.enum(["true", "false"]).optional(),
  APP_URL: z.string().url().default("http://localhost:3000"),
  SESSION_SECRET: z.string().min(32).optional(),

  DATABASE_URL: z.string().optional(),
  MAPIO_DEV_DB_DIR: z.string().optional(), // "memory" for an in-memory PGlite

  NEXT_PUBLIC_SUPABASE_URL: z.string().url().optional(),
  NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY: z.string().optional(),
  SUPABASE_SECRET_KEY: z.string().optional(),

  MAP_STYLE_PROVIDER: z.enum(["maptiler", "openfreemap", "fixture"]).optional(),
  NEXT_PUBLIC_MAPTILER_KEY: z.string().optional(),
  MAPTILER_STYLE_DARK: z.string().default("streets-v2-dark"),
  MAPTILER_STYLE_LIGHT: z.string().default("streets-v2"),

  GEOAPIFY_API_KEY: z.string().optional(),
  OPEN_METEO_API_KEY: z.string().optional(),
  OPEN_METEO_ALLOW_FREE_TIER: z.enum(["true", "false"]).optional(),
  OPEN_CHARGE_MAP_API_KEY: z.string().optional(),
  FX_PROVIDER: z.enum(["nbg", "fixture"]).optional(),

  BILLING_PROVIDER: z.enum(["paddle", "dev"]).optional(),
  PADDLE_ENVIRONMENT: z.enum(["sandbox", "production"]).default("sandbox"),
  PADDLE_API_KEY: z.string().optional(),
  PADDLE_WEBHOOK_SECRET: z.string().optional(),
  PADDLE_PRICE_ID: z.string().optional(),
  PADDLE_PRICE_ID_NO_TRIAL: z.string().optional(),
  NEXT_PUBLIC_PADDLE_CLIENT_TOKEN: z.string().optional(),
  DEV_BILLING_WEBHOOK_SECRET: z.string().default("dev_only_webhook_secret_change_me"),

  DISPLAY_PRICE_AMOUNT_MINOR: z.coerce.number().int().positive().default(600),
  DISPLAY_PRICE_CURRENCY: z.string().regex(/^[A-Z]{3}$/).default("USD"),
  TRIAL_DAYS: z.coerce.number().int().min(0).max(30).default(7),

  CRON_SECRET: z.string().optional(),
  ADMIN_EMAILS: z.string().default(""),
});

export type Env = z.infer<typeof schema>;

let cached: Env | null = null;
export function env(): Env {
  if (!cached) cached = schema.parse(process.env);
  return cached;
}
/** For tests only. */
export function resetEnvCache() {
  cached = null;
}

export function isProduction() {
  return env().NODE_ENV === "production";
}
export function demoAllowed() {
  return !isProduction() || env().MAPIO_DEMO_MODE === "true";
}

export class ConfigError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ConfigError";
  }
}
function requireDemo(what: string): void {
  if (!demoAllowed()) {
    throw new ConfigError(
      `${what} is not configured and demo adapters are disabled in production. ` +
        `Set the credentials listed in .env.example (or MAPIO_DEMO_MODE=true for a labelled demo deploy).`,
    );
  }
}

export type Mode = "live" | "demo";

export function authMode(): Mode {
  const e = env();
  if (e.NEXT_PUBLIC_SUPABASE_URL && e.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY) return "live";
  requireDemo("Supabase Auth");
  return "demo";
}
export function dbMode(): "postgres" | "pglite" {
  if (env().DATABASE_URL) return "postgres";
  requireDemo("DATABASE_URL");
  return "pglite";
}
export function billingMode(): Mode {
  const e = env();
  if (e.BILLING_PROVIDER === "dev") {
    requireDemo("Paddle billing");
    return "demo";
  }
  if (e.PADDLE_API_KEY && e.PADDLE_WEBHOOK_SECRET && e.PADDLE_PRICE_ID) return "live";
  requireDemo("Paddle billing");
  return "demo";
}
export function mapStyleProvider(): "maptiler" | "openfreemap" | "fixture" {
  const e = env();
  if (e.MAP_STYLE_PROVIDER) {
    if (e.MAP_STYLE_PROVIDER !== "maptiler") requireDemo("MapTiler map tiles");
    return e.MAP_STYLE_PROVIDER;
  }
  if (e.NEXT_PUBLIC_MAPTILER_KEY) return "maptiler";
  requireDemo("MapTiler map tiles");
  return "openfreemap";
}
export function geoMode(): Mode {
  if (env().GEOAPIFY_API_KEY) return "live";
  requireDemo("Geoapify search/routing");
  return "demo";
}
export function weatherMode(): "live" | "free-tier" | "demo" {
  const e = env();
  if (e.OPEN_METEO_API_KEY) return "live";
  if (e.OPEN_METEO_ALLOW_FREE_TIER === "true" && !isProduction()) return "free-tier";
  requireDemo("Open-Meteo weather");
  return "demo";
}
export function fxMode(): Mode {
  const e = env();
  if (e.FX_PROVIDER === "fixture") {
    requireDemo("NBG exchange rates");
    return "demo";
  }
  // NBG is a public official service: live by default, no key required.
  return "live";
}
export function chargersMode(): Mode {
  if (env().OPEN_CHARGE_MAP_API_KEY) return "live";
  requireDemo("Open Charge Map");
  return "demo";
}

export function sessionSecret(): string {
  const s = env().SESSION_SECRET;
  if (s) return s;
  if (isProduction() && env().MAPIO_DEMO_MODE !== "true") {
    throw new ConfigError("SESSION_SECRET (>= 32 chars) is required in production.");
  }
  return "dev-only-insecure-session-secret-0123456789abcdef";
}

export function adminEmails(): string[] {
  return env()
    .ADMIN_EMAILS.split(",")
    .map((s) => s.trim().toLowerCase())
    .filter(Boolean);
}

/** Summary used by the admin console and STATUS banner. No secrets. */
export function integrationSummary() {
  const safe = <T,>(fn: () => T): T | "misconfigured" => {
    try {
      return fn();
    } catch {
      return "misconfigured";
    }
  };
  return {
    auth: safe(authMode),
    database: safe(dbMode),
    billing: safe(billingMode),
    paddleEnvironment: env().PADDLE_ENVIRONMENT,
    map: safe(mapStyleProvider),
    geocodingRouting: safe(geoMode),
    weather: safe(weatherMode),
    fx: safe(fxMode),
    chargers: safe(chargersMode),
  };
}
