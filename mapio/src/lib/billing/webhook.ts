import { z } from "zod";
import type { Tx } from "@/lib/db";
import { hmac, sha256 } from "@/lib/auth/crypto";

/** Paddle notification envelope (same shape for real Paddle and the dev simulator). */
export const envelopeSchema = z.object({
  event_id: z.string().min(1),
  event_type: z.string().min(1),
  occurred_at: z.string().min(1),
  notification_id: z.string().optional(),
  data: z.record(z.string(), z.unknown()),
});
export type PaddleEnvelope = z.infer<typeof envelopeSchema>;

const period = z.object({ starts_at: z.string(), ends_at: z.string() }).nullish();
export const subscriptionDataSchema = z.object({
  id: z.string(),
  status: z.enum(["trialing", "active", "past_due", "paused", "canceled"]),
  customer_id: z.string().nullish(),
  currency_code: z.string().nullish(),
  next_billed_at: z.string().nullish(),
  canceled_at: z.string().nullish(),
  paused_at: z.string().nullish(),
  updated_at: z.string().nullish(),
  current_billing_period: period,
  billing_cycle: z.object({ interval: z.string(), frequency: z.number() }).nullish(),
  scheduled_change: z
    .object({ action: z.enum(["cancel", "pause", "resume"]), effective_at: z.string() })
    .nullish(),
  custom_data: z.record(z.string(), z.unknown()).nullish(),
  items: z
    .array(
      z.object({
        trial_dates: period,
        price: z
          .object({
            id: z.string(),
            product_id: z.string().nullish(),
            unit_price: z.object({ amount: z.string(), currency_code: z.string() }).nullish(),
          })
          .nullish(),
      }),
    )
    .default([]),
});
export type PaddleSubscriptionData = z.infer<typeof subscriptionDataSchema>;

/** custom_data we attach at checkout; the signature stops forged user ids. */
export function signedCustomData(secret: string, userId: string) {
  return { mapio_user_id: userId, mapio_sig: hmac(secret, `mapio:${userId}`) };
}
export function userIdFromCustomData(secret: string, cd: Record<string, unknown> | null | undefined) {
  const uid = typeof cd?.mapio_user_id === "string" ? cd.mapio_user_id : null;
  const sig = typeof cd?.mapio_sig === "string" ? cd.mapio_sig : null;
  if (!uid || !sig || hmac(secret, `mapio:${uid}`) !== sig) return null;
  return uid;
}

export type ApplyOutcome = "applied" | "stale" | "ignored" | "duplicate";

/**
 * Applies one verified notification inside a service transaction.
 * - Idempotent: processed_billing_events.provider_event_id is the dedupe key.
 * - Out-of-order safe: events older than subscriptions.last_event_at are recorded as
 *   "stale" and not applied (the newer state already wins).
 */
export async function applyBillingEvent(
  tx: Tx,
  env: PaddleEnvelope,
  opts: { provider: "paddle" | "dev"; customDataSecret: string },
): Promise<ApplyOutcome> {
  const dup = await tx.query(
    `insert into public.processed_billing_events (provider_event_id, provider, event_type, occurred_at, outcome)
     values ($1, $2, $3, $4, 'applied')
     on conflict (provider_event_id) do nothing returning provider_event_id`,
    [env.event_id, opts.provider, env.event_type, env.occurred_at],
  );
  if (!dup.length) return "duplicate";

  const record = async (outcome: ApplyOutcome, subId?: string) => {
    await tx.query(
      `update public.processed_billing_events set outcome = $2, provider_subscription_id = $3
       where provider_event_id = $1`,
      [env.event_id, outcome, subId ?? null],
    );
    return outcome;
  };

  if (!env.event_type.startsWith("subscription.")) return record("ignored");
  const parsed = subscriptionDataSchema.safeParse(env.data);
  if (!parsed.success) return record("ignored");
  const sub = parsed.data;

  const [existing] = await tx.query<{ user_id: string; last_event_at: Date | null }>(
    `select user_id, last_event_at from public.subscriptions where provider = $1 and provider_subscription_id = $2`,
    [opts.provider, sub.id],
  );
  const userId = existing?.user_id ?? userIdFromCustomData(opts.customDataSecret, sub.custom_data);
  if (!userId) return record("ignored", sub.id);
  const [user] = await tx.query<{ email: string }>(`select email from auth.users where id = $1`, [userId]);
  if (!user) return record("ignored", sub.id);

  if (existing?.last_event_at && new Date(existing.last_event_at).getTime() > new Date(env.occurred_at).getTime()) {
    return record("stale", sub.id);
  }

  const row = mapSubscription(sub);
  await tx.query(
    `insert into public.subscriptions (
       user_id, provider, provider_customer_id, provider_subscription_id, status, price_id, product_id,
       unit_price_amount, currency_code, billing_interval, trial_start, trial_end,
       current_period_start, current_period_end, next_billed_at,
       scheduled_change_action, scheduled_change_effective_at, canceled_at, paused_at,
       last_event_at, last_event_id)
     values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20,$21)
     on conflict (provider, provider_subscription_id) do update set
       provider_customer_id = excluded.provider_customer_id, status = excluded.status,
       price_id = excluded.price_id, product_id = excluded.product_id,
       unit_price_amount = excluded.unit_price_amount, currency_code = excluded.currency_code,
       billing_interval = excluded.billing_interval,
       trial_start = coalesce(excluded.trial_start, public.subscriptions.trial_start),
       trial_end = coalesce(excluded.trial_end, public.subscriptions.trial_end),
       current_period_start = excluded.current_period_start, current_period_end = excluded.current_period_end,
       next_billed_at = excluded.next_billed_at,
       scheduled_change_action = excluded.scheduled_change_action,
       scheduled_change_effective_at = excluded.scheduled_change_effective_at,
       canceled_at = excluded.canceled_at, paused_at = excluded.paused_at,
       last_event_at = excluded.last_event_at, last_event_id = excluded.last_event_id`,
    [
      userId, opts.provider, row.provider_customer_id, sub.id, row.status, row.price_id, row.product_id,
      row.unit_price_amount, row.currency_code, row.billing_interval, row.trial_start, row.trial_end,
      row.current_period_start, row.current_period_end, row.next_billed_at,
      row.scheduled_change_action, row.scheduled_change_effective_at, row.canceled_at, row.paused_at,
      env.occurred_at, env.event_id,
    ],
  );
  if (row.trial_start) {
    await tx.query(
      `insert into public.trial_claims (email_hash, user_id) values ($1, $2) on conflict (email_hash) do nothing`,
      [emailHash(user.email), userId],
    );
  }
  return record("applied", sub.id);
}

export function emailHash(email: string) {
  return sha256(`mapio-trial:${email.trim().toLowerCase()}`);
}

export function mapSubscription(sub: PaddleSubscriptionData) {
  const item = sub.items[0];
  return {
    provider_customer_id: sub.customer_id ?? null,
    status: sub.status,
    price_id: item?.price?.id ?? null,
    product_id: item?.price?.product_id ?? null,
    unit_price_amount: item?.price?.unit_price ? Number(item.price.unit_price.amount) : null,
    currency_code: item?.price?.unit_price?.currency_code ?? sub.currency_code ?? null,
    billing_interval: sub.billing_cycle ? `${sub.billing_cycle.frequency} ${sub.billing_cycle.interval}` : null,
    trial_start: item?.trial_dates?.starts_at ?? null,
    trial_end: item?.trial_dates?.ends_at ?? null,
    current_period_start: sub.current_billing_period?.starts_at ?? null,
    current_period_end: sub.current_billing_period?.ends_at ?? null,
    next_billed_at: sub.next_billed_at ?? null,
    scheduled_change_action: sub.scheduled_change?.action ?? null,
    scheduled_change_effective_at: sub.scheduled_change?.effective_at ?? null,
    canceled_at: sub.canceled_at ?? null,
    paused_at: sub.paused_at ?? null,
  };
}
