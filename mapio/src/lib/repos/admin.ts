import "server-only";
import { withService, withUser } from "@/lib/db";
import { DAILY_QUOTA } from "@/lib/providers/usage";

export async function isAdmin(userId: string) {
  const [r] = await withUser(userId, (tx) =>
    tx.query<{ role: string }>(`select role from public.profiles where id = $1`, [userId]),
  );
  return r?.role === "admin";
}

/** Aggregates only — no emails, locations, or payment details. */
export async function adminMetrics() {
  return withService(async (tx) => {
    const one = async (sql: string) => Number((await tx.query<{ n: number }>(sql))[0]?.n ?? 0);
    const usage = await tx.query<{ provider: string; requests: number; cache_hits: number; errors: number; cost: number }>(
      `select provider, sum(requests)::int as requests, sum(cache_hits)::int as cache_hits,
              sum(errors)::int as errors, sum(approx_cost_micros)::bigint as cost
       from public.api_usage_daily where day = current_date group by provider order by provider`,
    );
    const statuses = await tx.query<{ status: string; n: number }>(
      `select case when scheduled_change_action = 'cancel' then 'scheduled_cancel' else status end as status,
              count(*)::int as n from public.subscriptions group by 1 order by 1`,
    );
    return {
      users: await one(`select count(*)::int as n from auth.users`),
      newUsers7d: await one(`select count(*)::int as n from auth.users where created_at > now() - interval '7 days'`),
      activeTrials: await one(`select count(*)::int as n from public.subscriptions where status = 'trialing'`),
      activeSubscriptions: await one(`select count(*)::int as n from public.subscriptions where status = 'active'`),
      pairedDevices: await one(`select count(*)::int as n from public.paired_devices where revoked_at is null`),
      inbox7d: await one(`select count(*)::int as n from public.inbox_destinations where sent_at > now() - interval '7 days'`),
      webhookEvents24h: await one(
        `select count(*)::int as n from public.processed_billing_events where processed_at > now() - interval '1 day'`,
      ),
      webhookIssues24h: await one(
        `select count(*)::int as n from public.processed_billing_events
         where processed_at > now() - interval '1 day' and outcome in ('ignored','error')`,
      ),
      statuses,
      usage: usage.map((u) => ({
        ...u,
        quota: DAILY_QUOTA[u.provider] ?? null,
        overQuotaPct: DAILY_QUOTA[u.provider] ? Math.round((u.requests / DAILY_QUOTA[u.provider]) * 100) : null,
      })),
    };
  });
}

export function listFlags() {
  return withService((tx) =>
    tx.query<{ key: string; enabled: boolean; description: string | null; updated_at: string }>(
      `select key, enabled, description, updated_at from public.feature_flags order by key`,
    ),
  );
}

export async function getFlags(): Promise<Record<string, boolean>> {
  const rows = await listFlags();
  return Object.fromEntries(rows.map((r) => [r.key, r.enabled]));
}

export async function setFlag(actorId: string, key: string, enabled: boolean) {
  await withService(async (tx) => {
    await tx.query(`update public.feature_flags set enabled = $2, updated_at = now(), updated_by = $3 where key = $1`, [
      key,
      enabled,
      actorId,
    ]);
    await tx.query(
      `insert into public.admin_audit_log (actor_id, action, details) values ($1, 'flag.set', $2)`,
      [actorId, JSON.stringify({ key, enabled })],
    );
  });
}

export async function grantAccess(actorId: string, targetEmail: string, days: number, reason: string) {
  return withService(async (tx) => {
    const [u] = await tx.query<{ id: string }>(`select id from auth.users where email = $1`, [targetEmail.trim().toLowerCase()]);
    if (!u) throw new Error("No user with that email.");
    await tx.query(
      `insert into public.access_grants (user_id, granted_by, reason, expires_at) values ($1, $2, $3, now() + ($4 || ' days')::interval)`,
      [u.id, actorId, reason, String(days)],
    );
    await tx.query(
      `insert into public.admin_audit_log (actor_id, action, target_user_id, details) values ($1, 'access.grant', $2, $3)`,
      [actorId, u.id, JSON.stringify({ days, reason })],
    );
  });
}

export function recentAudit(limit = 20) {
  return withService((tx) =>
    tx.query<{ id: string; action: string; created_at: string; details: unknown; actor_id: string | null }>(
      `select id, action, created_at, details, actor_id from public.admin_audit_log order by created_at desc limit $1`,
      [limit],
    ),
  );
}
