import { afterAll, beforeAll, describe, expect, it } from "vitest";
import crypto from "node:crypto";
import { closeDb, withService } from "@/lib/db";
import { devSimulate, getBillingState, handleWebhook } from "@/lib/billing";
import { signatureHeader } from "@/lib/billing/signature";
import { signedCustomData } from "@/lib/billing/webhook";
import { createUser } from "../support/db";

const SECRET = "dev_only_webhook_secret_change_me"; // env default for DEV_BILLING_WEBHOOK_SECRET
let user: string;

const sub = (over: Record<string, unknown> = {}, userId = user) => ({
  id: "sub_test_1",
  status: "trialing",
  customer_id: "ctm_1",
  currency_code: "USD",
  next_billed_at: "2026-11-16T00:00:00Z",
  current_billing_period: { starts_at: "2026-11-09T00:00:00Z", ends_at: "2026-11-16T00:00:00Z" },
  billing_cycle: { interval: "month", frequency: 1 },
  scheduled_change: null,
  custom_data: signedCustomData(SECRET, userId),
  items: [
    {
      trial_dates: { starts_at: "2026-11-09T00:00:00Z", ends_at: "2026-11-16T00:00:00Z" },
      price: { id: "pri_1", product_id: "pro_1", unit_price: { amount: "600", currency_code: "USD" } },
    },
  ],
  ...over,
});

function envelope(type: string, data: object, occurred: string, id = `evt_${crypto.randomUUID()}`) {
  return JSON.stringify({ event_id: id, event_type: type, occurred_at: occurred, data });
}
async function deliver(body: string, secret = SECRET) {
  return handleWebhook(body, signatureHeader(secret, body), "dev");
}

beforeAll(async () => {
  user = await createUser("billing@example.test");
});
afterAll(closeDb);

describe("billing webhooks → entitlement", () => {
  it("rejects unsigned / wrongly signed webhooks without touching the database", async () => {
    const body = envelope("subscription.created", sub(), "2026-11-09T00:00:00Z");
    expect((await handleWebhook(body, null, "dev")).status).toBe(401);
    expect((await deliver(body, "wrong-secret")).status).toBe(401);
    const s = await getBillingState(user);
    expect(s.entitlement.hasAccess).toBe(false);
  });

  it("checkout redirect alone grants nothing; a verified trialing webhook grants access", async () => {
    expect((await getBillingState(user)).entitlement.hasAccess).toBe(false);
    const r = await deliver(envelope("subscription.created", sub(), "2026-11-09T00:00:01Z", "evt_created"));
    expect(r).toMatchObject({ status: 200, outcome: "applied" });
    const s = await getBillingState(user, "billing@example.test");
    expect(s.entitlement).toMatchObject({ hasAccess: true, status: "trialing" });
    expect(s.displayPrice).toEqual({ amountMinor: 600, currency: "USD", source: "provider" });
    expect(s.trialEligible).toBe(false); // trial claimed
  });

  it("duplicate event ids are ignored", async () => {
    const r = await deliver(envelope("subscription.created", sub(), "2026-11-09T00:00:01Z", "evt_created"));
    expect(r.outcome).toBe("duplicate");
  });

  it("trial → active conversion", async () => {
    const r = await deliver(
      envelope(
        "subscription.activated",
        sub({ status: "active", current_billing_period: { starts_at: "2026-11-16T00:00:00Z", ends_at: "2026-12-16T00:00:00Z" } }),
        "2026-11-16T00:00:05Z",
      ),
    );
    expect(r.outcome).toBe("applied");
    expect((await getBillingState(user)).entitlement.status).toBe("active");
  });

  it("out-of-order (older) events do not overwrite newer state", async () => {
    const r = await deliver(envelope("subscription.updated", sub({ status: "trialing" }), "2026-11-10T00:00:00Z"));
    expect(r.outcome).toBe("stale");
    expect((await getBillingState(user)).entitlement.status).toBe("active");
  });

  it("scheduled cancel keeps access, final cancel removes it", async () => {
    await deliver(
      envelope(
        "subscription.updated",
        sub({ status: "active", scheduled_change: { action: "cancel", effective_at: "2026-12-16T00:00:00Z" } }),
        "2026-11-20T00:00:00Z",
      ),
    );
    const s = await getBillingState(user);
    expect(s.entitlement).toMatchObject({ hasAccess: true, status: "scheduled_cancel" });
    await deliver(envelope("subscription.canceled", sub({ status: "canceled", canceled_at: "2026-12-16T00:00:00Z" }), "2026-12-16T00:00:01Z"));
    expect((await getBillingState(user)).entitlement).toMatchObject({ hasAccess: false, status: "canceled" });
  });

  it("forged custom_data (bad signature) for an unknown subscription is ignored", async () => {
    const victim = await createUser("victim@example.test");
    const forged = sub({ id: "sub_forged", custom_data: { mapio_user_id: victim, mapio_sig: "forged" } });
    const r = await deliver(envelope("subscription.created", forged, "2026-11-09T00:00:00Z"));
    expect(r.outcome).toBe("ignored");
    expect((await getBillingState(victim)).entitlement.hasAccess).toBe(false);
  });

  it("dev simulator drives the full lifecycle through signed webhooks", async () => {
    const u = await createUser("sim@example.test");
    expect(await devSimulate(u, "start_trial")).toBe("applied");
    expect((await getBillingState(u)).entitlement.status).toBe("trialing");
    await devSimulate(u, "schedule_cancel");
    expect((await getBillingState(u)).entitlement.status).toBe("scheduled_cancel");
    await devSimulate(u, "undo_cancel");
    expect((await getBillingState(u)).entitlement.status).toBe("trialing");
    await devSimulate(u, "convert_trial");
    expect((await getBillingState(u)).entitlement.status).toBe("active");
    await devSimulate(u, "fail_payment");
    expect((await getBillingState(u)).entitlement).toMatchObject({ status: "past_due", hasAccess: true });
    await devSimulate(u, "recover_payment");
    await devSimulate(u, "cancel_now");
    expect((await getBillingState(u)).entitlement.hasAccess).toBe(false);
    const events = await withService((tx) =>
      tx.query(`select count(*)::int as n from public.processed_billing_events where provider = 'dev'`),
    );
    expect(Number((events[0] as { n: number }).n)).toBeGreaterThanOrEqual(7);
  });
});
