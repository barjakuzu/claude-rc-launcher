import "server-only";
import fs from "node:fs";
import path from "node:path";
import { dbMode, env } from "@/lib/env";

/**
 * Minimal SQL access layer shared by production (Supabase Postgres via `pg`) and
 * local dev/tests (PGlite running the same migrations).
 *
 *  - withUser(userId, fn): runs `fn` in a transaction as role `authenticated` with the
 *    user's JWT subject set, so Row Level Security applies to server code as well.
 *  - withService(fn): privileged transaction (table owner). ONLY for webhooks,
 *    cron jobs, pairing redemption checks and admin aggregates.
 */
export interface Tx {
  query<T = Record<string, unknown>>(sql: string, params?: unknown[]): Promise<T[]>;
}
interface Driver {
  transaction<R>(fn: (tx: Tx) => Promise<R>): Promise<R>;
  close(): Promise<void>;
}

const MIGRATIONS_DIR = path.join(process.cwd(), "supabase", "migrations");

async function createPglite(): Promise<Driver> {
  const { PGlite } = await import("@electric-sql/pglite");
  const { pgcrypto } = await import("@electric-sql/pglite/contrib/pgcrypto");
  const dir = env().MAPIO_DEV_DB_DIR ?? path.join(process.cwd(), ".data", "pglite");
  if (dir !== "memory") fs.mkdirSync(dir, { recursive: true });
  const db = await PGlite.create({
    dataDir: dir === "memory" ? undefined : dir,
    extensions: { pgcrypto },
  });
  await runDevMigrations((sql) => db.exec(sql).then(() => undefined), (sql, p) =>
    db.query(sql, p).then((r) => r.rows as Record<string, unknown>[]),
  );
  return {
    transaction: (fn) =>
      db.transaction(async (t) =>
        fn({ query: async (sql, params) => (await t.query(sql, params)).rows as never }),
      ),
    close: () => db.close(),
  };
}

/** Applies dev-shim + every migration file once, tracked in _mapio_migrations. */
export async function runDevMigrations(
  exec: (sql: string) => Promise<void>,
  query: (sql: string, params?: unknown[]) => Promise<Record<string, unknown>[]>,
) {
  const shim = fs.readFileSync(path.join(process.cwd(), "src", "lib", "db", "dev-shim.sql"), "utf8");
  await exec(shim);
  await exec(
    `create table if not exists public._mapio_migrations (name text primary key, applied_at timestamptz not null default now())`,
  );
  const applied = new Set((await query(`select name from public._mapio_migrations`)).map((r) => String(r.name)));
  const files = fs.readdirSync(MIGRATIONS_DIR).filter((f) => f.endsWith(".sql")).sort();
  for (const f of files) {
    if (applied.has(f)) continue;
    await exec(fs.readFileSync(path.join(MIGRATIONS_DIR, f), "utf8"));
    await query(`insert into public._mapio_migrations (name) values ($1)`, [f]);
  }
}

async function createPg(): Promise<Driver> {
  const { Pool } = await import("pg");
  const url = env().DATABASE_URL!;
  const local = /@(localhost|127\.0\.0\.1)[:/]/.test(url);
  const pool = new Pool({
    connectionString: url,
    max: 5,
    ssl: local ? undefined : { rejectUnauthorized: false },
    statement_timeout: 10_000,
  });
  return {
    async transaction(fn) {
      const client = await pool.connect();
      try {
        await client.query("begin");
        const result = await fn({ query: async (sql, params) => (await client.query(sql, params as unknown[])).rows });
        await client.query("commit");
        return result;
      } catch (e) {
        await client.query("rollback").catch(() => {});
        throw e;
      } finally {
        client.release();
      }
    },
    close: () => pool.end(),
  };
}

const g = globalThis as unknown as { __mapioDb?: Promise<Driver> };
function driver(): Promise<Driver> {
  if (!g.__mapioDb) {
    g.__mapioDb = (dbMode() === "postgres" ? createPg() : createPglite()).catch((e) => {
      g.__mapioDb = undefined;
      throw e;
    });
  }
  return g.__mapioDb;
}

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export async function withUser<R>(userId: string, fn: (tx: Tx) => Promise<R>): Promise<R> {
  if (!UUID_RE.test(userId)) throw new Error("withUser: invalid user id");
  return (await driver()).transaction(async (tx) => {
    await tx.query("set local role authenticated");
    await tx.query(
      `select set_config('request.jwt.claim.sub', $1, true),
              set_config('request.jwt.claims', $2, true)`,
      [userId, JSON.stringify({ sub: userId, role: "authenticated" })],
    );
    return fn(tx);
  });
}

export async function withService<R>(fn: (tx: Tx) => Promise<R>): Promise<R> {
  return (await driver()).transaction(fn);
}

/** Tests only. */
export async function closeDb() {
  if (g.__mapioDb) {
    const d = await g.__mapioDb;
    g.__mapioDb = undefined;
    await d.close();
  }
}
