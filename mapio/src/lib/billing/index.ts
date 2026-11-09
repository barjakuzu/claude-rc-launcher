import "server-only";
import crypto from "node:crypto";
import { billingMode, env } from "@/lib/env";
import { withService, withUser } from "@/lib/db";
import { computeEntitlement, type Entitlement, type GrantRow, type SubscriptionRow } from "./entitlement";
import { applyBillingEvent, emailHash, envelopeSchema, signedCustomData, type ApplyOutcome } from "./webhook";
import { signatureHeader, verifySignature } from "./signature";
import { fetchJson } from "@/lib/providers/http";

/* ───────────────────────────── shared ───────────────────────────── */

export type BillingRow = SubscriptionRow & {
  id: string;
  provider: "paddle" | "dev";
  provider_customer_id: string | null;
  provider_subscription_id: string;
  unit_price_amount: string | number | null;
  currency_code: string | null;
  billing_interval: string | null;
  trial_start: string | Date | null;
  canceled_at: string | Date | null;
  updated_at: string | Date;
};

export interface BillingState {
  mode: "live" | "demo";
  environment: "sandbox" | "production" | "simulator";
  entitlement: Entitlement;
  subscription: BillingRow | null;
  trialEligible: boolean;
  displayPrice: { amountMinor: number; currency: string; source: "provider" | "configured" };
  trialDays: number;
}

function customDataSecret() {
  return env().PADDLE_WEBHOOK_SECRET ?? env().DEV_BILLING_WEBHOOK_SECRET;
}

export async function getEntitlement(userId: string): Promise<Entitlement> {
  return (await getBillingState(userId)).entitlement;
}

export async function getBillingState(userId: string, email?: string): Promise<BillingState> {
  const mode = billingMode();
  const { subs, grants } = await withUser(userId, async (tx) => ({
    subs: await tx.query<BillingRow>(`select * from public.subscriptions order by updated_at desc`),
    grants: await tx.query<GrantRow>(`select expires_at, revoked_at from public.access_grants`),
  }));
  const entitlement = computeEntitlement(subs, grants);
  const current = subs[0] ?? null;
  let trialEligible = !subs.some((s) => s.trial_start);
  if (trialEligible && email) {
    const claim = await withService((tx) =>
      tx.query(`select 1 from public.trial_claims where email_hash = $1`, [emailHash(email)]),
    );
    trialEligible = claim.length === 0;
  }
  const e = env();
  const priced = current?.unit_price_amount != null && current.currency_code;
  return {
    mode,
    environment: mode === "demo" ? "simulator" : e.PADDLE_ENVIRONMENT,
    entitlement,
    subscription: current,
    trialEligible,
    displayPrice: priced
      ? { amountMinor: Number(current!.unit_price_amount), currency: current!.currency_code!, source: "provider" }
      : { amountMinor: e.DISPLAY_PRICE_AMOUNT_MINOR, currency: e.DISPLAY_PRICE_CURRENCY, source: "configured" },
    trialDays: e.TRIAL_DAYS,
  };
}

/** Verifies + applies a raw webhook body. Used by the HTTP route and the dev simulator. */
export async function handleWebhook(
  rawBody: string,
  signature: string | null,
  provider: "paddle" | "dev",
): Promise<{ status: number; outcome?: ApplyOutcome; error?: string }> {
  const secret = provider === "paddle" ? env().PADDLE_WEBHOOK_SECRET : env().DEV_BILLING_WEBHOOK_SECRET;
  if (!secret) return { status: 500, error: "webhook secret not configured" };
  if (!verifySignature(secret, rawBody, signature)) return { status: 401, error: "invalid signature" };
  let json: unknown;
  try {
    json = JSON.parse(rawBody);
  } catch {
    return { status: 400, error: "invalid json" };
  }
  const parsed = envelopeSchema.safeParse(json);
  if (!parsed.success) return { status: 400, error: "invalid envelope" };
  const outcome = await withService((tx) =>
    applyBillingEvent(tx, parsed.data, { provider, customDataSecret: customDataSecret() }),
  );
  return { status: 200, outcome };
}

/* ───────────────────────────── Paddle (live) ───────────────────────────── */

function paddleBase() {
  return env().PADDLE_ENVIRONMENT === "production" ? "https://api.paddle.com" : "https://sandbox-api.paddle.com";
}
async function paddle<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetchJson<{ data: T }>(`${paddleBase()}${path}`, {
    method,
    headers: { Authorization: `Bearer ${env().PADDLE_API_KEY}`, "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
    timeoutMs: 10_000,
    retries: method === "GET" ? 2 : 0,
  });
  return res.data;
}

/** Applies a subscription object fetched from the Paddle API as a synthetic, ordered event. */
async function applyApiSubscription(data: Record<string, unknown>) {
  const occurred = String(data.updated_at ?? new Date().toISOString());
  return withService((tx) =>
    applyBillingEvent(
      tx,
      { event_id: `api:${data.id}:${occurred}`, event_type: "subscription.updated", occurred_at: occurred, data },
      { provider: "paddle", customDataSecret: customDataSecret() },
    ),
  );
}

export type CheckoutStart =
  | { kind: "paddle"; transactionId: string; clientToken: string; environment: "sandbox" | "production" }
  | { kind: "dev"; url: string }
  | { kind: "error"; message: string };

export async function startCheckout(user: { id: string; email: string }): Promise<CheckoutStart> {
  const state = await getBillingState(user.id, user.email);
  if (state.entitlement.hasAccess && state.entitlement.reason !== "admin_grant") {
    return { kind: "error", message: "You already have an active MAPIO Premium subscription." };
  }
  if (state.mode === "demo") return { kind: "dev", url: "/dev/billing/checkout" };

  const e = env();
  const priceId = state.trialEligible ? e.PADDLE_PRICE_ID! : e.PADDLE_PRICE_ID_NO_TRIAL;
  if (!priceId) {
    return {
      kind: "error",
      message: "Your free trial has already been used. Please contact support to subscribe.",
    };
  }
  if (!e.NEXT_PUBLIC_PADDLE_CLIENT_TOKEN) return { kind: "error", message: "Checkout is not configured." };
  const existingCustomer = state.subscription?.provider_customer_id ?? undefined;
  const txn = await paddle<{ id: string }>("POST", "/transactions", {
    items: [{ price_id: priceId, quantity: 1 }],
    ...(existingCustomer ? { customer_id: existingCustomer } : {}),
    custom_data: signedCustomData(customDataSecret(), user.id),
  });
  return {
    kind: "paddle",
    transactionId: txn.id,
    clientToken: e.NEXT_PUBLIC_PADDLE_CLIENT_TOKEN,
    environment: e.PADDLE_ENVIRONMENT,
  };
}

export async function portalUrl(userId: string): Promise<string | null> {
  const state = await getBillingState(userId);
  const sub = state.subscription;
  if (!sub) return null;
  if (state.mode === "demo") return "/dev/billing/portal";
  if (!sub.provider_customer_id) return null;
  const session = await paddle<{ urls: { general: { overview: string } } }>(
    "POST",
    `/customers/${encodeURIComponent(sub.provider_customer_id)}/portal-sessions`,
    { subscription_ids: [sub.provider_subscription_id] },
  );
  return session.urls.general.overview;
}

export async function scheduleCancel(userId: string) {
  const state = await getBillingState(userId);
  const sub = state.subscription;
  if (!sub || !["trialing", "active", "past_due"].includes(sub.status)) throw new Error("No cancellable subscription.");
  if (state.mode === "demo") return devSimulate(userId, "schedule_cancel");
  const data = await paddle<Record<string, unknown>>(
    "POST",
    `/subscriptions/${encodeURIComponent(sub.provider_subscription_id)}/cancel`,
    { effective_from: "next_billing_period" },
  );
  return applyApiSubscription(data);
}

export async function undoScheduledCancel(userId: string) {
  const state = await getBillingState(userId);
  const sub = state.subscription;
  if (!sub || sub.scheduled_change_action !== "cancel") throw new Error("Nothing to undo.");
  if (state.mode === "demo") return devSimulate(userId, "undo_cancel");
  const data = await paddle<Record<string, unknown>>(
    "PATCH",
    `/subscriptions/${encodeURIComponent(sub.provider_subscription_id)}`,
    { scheduled_change: null },
  );
  return applyApiSubscription(data);
}

/** Periodic safety net for missed webhooks (Vercel Cron → /api/cron/reconcile). */
export async function reconcileSubscriptions(limit = 100) {
  if (billingMode() !== "live") return { checked: 0, applied: 0 };
  const rows = await withService((tx) =>
    tx.query<{ provider_subscription_id: string }>(
      `select provider_subscription_id from public.subscriptions
       where provider = 'paddle' and status <> 'canceled' order by updated_at asc limit $1`,
      [limit],
    ),
  );
  let applied = 0;
  for (const r of rows) {
    const data = await paddle<Record<string, unknown>>(
      "GET",
      `/subscriptions/${encodeURIComponent(r.provider_subscription_id)}`,
    );
    if ((await applyApiSubscription(data)) === "applied") applied++;
  }
  return { checked: rows.length, applied };
}

/* ───────────────────────────── Dev simulator ───────────────────────────── */

export type DevAction =
  | "start_trial"
  | "convert_trial"
  | "schedule_cancel"
  | "undo_cancel"
  | "cancel_now"
  | "fail_payment"
  | "recover_payment"
  | "expire_trial_unpaid";

/**
 * DEV ONLY. Produces Paddle-shaped subscription notifications, signs them with
 * DEV_BILLING_WEBHOOK_SECRET and feeds them through the exact same verification +
 * apply pipeline as real webhooks. No money moves; the UI labels this clearly.
 */
export async function devSimulate(userId: string, action: DevAction) {
  if (billingMode() !== "demo") throw new Error("The billing simulator is only available in demo mode.");
  const e = env();
  const [current] = await withService((tx) =>
    tx.query<BillingRow>(
      `select * from public.subscriptions where user_id = $1 and provider = 'dev' order by updated_at desc limit 1`,
      [userId],
    ),
  );
  const now = new Date();
  const iso = (d: Date) => d.toISOString();
  const addDays = (d: Date, n: number) => new Date(d.getTime() + n * 86_400_000);
  const addMonth = (d: Date) => {
    const x = new Date(d);
    x.setUTCMonth(x.getUTCMonth() + 1);
    return x;
  };
  const toIso = (v: unknown) => (v ? new Date(v as string).toISOString() : null);

  let data: Record<string, unknown>;
  let type: string;
  if (action === "start_trial") {
    if (current && current.status !== "canceled") throw new Error("A subscription already exists.");
    const trialEnd = addDays(now, e.TRIAL_DAYS);
    const trialing = e.TRIAL_DAYS > 0;
    data = devSub({
      id: `dev_sub_${crypto.randomBytes(6).toString("hex")}`,
      customer: `dev_ctm_${userId.slice(0, 8)}`,
      status: trialing ? "trialing" : "active",
      periodStart: now,
      periodEnd: trialing ? trialEnd : addMonth(now),
      next: trialing ? trialEnd : addMonth(now),
      trial: trialing ? { starts_at: iso(now), ends_at: iso(trialEnd) } : null,
      scheduled: null,
      userId,
    });
    type = "subscription.created";
  } else {
    if (!current) throw new Error("No simulated subscription yet.");
    const base = {
      id: current.provider_subscription_id,
      customer: current.provider_customer_id ?? "dev_ctm",
      status: current.status as string,
      periodStart: new Date(current.current_period_start ?? now),
      periodEnd: new Date(current.current_period_end ?? now),
      next: current.next_billed_at ? new Date(current.next_billed_at) : null,
      trial: current.trial_start ? { starts_at: toIso(current.trial_start)!, ends_at: toIso(current.trial_end)! } : null,
      scheduled: current.scheduled_change_action
        ? { action: current.scheduled_change_action, effective_at: toIso(current.scheduled_change_effective_at)! }
        : null,
      userId,
    };
    switch (action) {
      case "convert_trial":
        data = devSub({ ...base, status: "active", periodStart: now, periodEnd: addMonth(now), next: addMonth(now) });
        type = "subscription.activated";
        break;
      case "schedule_cancel":
        data = devSub({ ...base, scheduled: { action: "cancel", effective_at: iso(base.periodEnd) }, next: null });
        type = "subscription.updated";
        break;
      case "undo_cancel":
        data = devSub({ ...base, scheduled: null, next: base.periodEnd });
        type = "subscription.updated";
        break;
      case "cancel_now":
      case "expire_trial_unpaid":
        data = devSub({ ...base, status: "canceled", scheduled: null, next: null, canceledAt: now });
        type = "subscription.canceled";
        break;
      case "fail_payment":
        data = devSub({ ...base, status: "past_due", periodStart: now, periodEnd: addMonth(now) });
        type = "subscription.past_due";
        break;
      case "recover_payment":
        data = devSub({ ...base, status: "active" });
        type = "subscription.updated";
        break;
      default:
        throw new Error("Unknown action");
    }
  }
  const body = JSON.stringify({
    event_id: `evt_dev_${crypto.randomBytes(8).toString("hex")}`,
    event_type: type,
    occurred_at: iso(new Date()),
    notification_id: `ntf_dev_${crypto.randomBytes(6).toString("hex")}`,
    data,
  });
  const result = await handleWebhook(body, signatureHeader(e.DEV_BILLING_WEBHOOK_SECRET, body), "dev");
  if (result.status !== 200) throw new Error(result.error ?? "simulated webhook failed");
  return result.outcome!;
}

function devSub(o: {
  id: string;
  customer: string;
  status: string;
  periodStart: Date;
  periodEnd: Date;
  next: Date | null;
  trial: { starts_at: string; ends_at: string } | null;
  scheduled: { action: string; effective_at: string } | null;
  canceledAt?: Date;
  userId: string;
}) {
  const e = env();
  return {
    id: o.id,
    status: o.status,
    customer_id: o.customer,
    currency_code: e.DISPLAY_PRICE_CURRENCY,
    next_billed_at: o.next?.toISOString() ?? null,
    canceled_at: o.canceledAt?.toISOString() ?? null,
    paused_at: null,
    updated_at: new Date().toISOString(),
    current_billing_period: { starts_at: o.periodStart.toISOString(), ends_at: o.periodEnd.toISOString() },
    billing_cycle: { interval: "month", frequency: 1 },
    scheduled_change: o.scheduled,
    custom_data: signedCustomData(customDataSecret(), o.userId),
    items: [
      {
        trial_dates: o.trial,
        price: {
          id: "dev_pri_mapio_premium_monthly",
          product_id: "dev_pro_mapio_premium",
          unit_price: { amount: String(e.DISPLAY_PRICE_AMOUNT_MINOR), currency_code: e.DISPLAY_PRICE_CURRENCY },
        },
      },
    ],
  };
}
