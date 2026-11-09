import { describe, expect, it } from "vitest";
import { computeEntitlement, PAST_DUE_GRACE_DAYS, type SubscriptionRow } from "@/lib/billing/entitlement";

const now = new Date("2026-11-09T12:00:00Z");
const day = 86_400_000;
const base: SubscriptionRow = {
  status: "active",
  trial_end: null,
  current_period_start: new Date(now.getTime() - 10 * day),
  current_period_end: new Date(now.getTime() + 20 * day),
  next_billed_at: new Date(now.getTime() + 20 * day),
  scheduled_change_action: null,
  scheduled_change_effective_at: null,
};

describe("computeEntitlement", () => {
  it("no subscription → no access", () => {
    expect(computeEntitlement([], [], now)).toMatchObject({ hasAccess: false, status: "none" });
  });

  it("trialing → access until trial end", () => {
    const e = computeEntitlement([{ ...base, status: "trialing", trial_end: new Date(now.getTime() + 3 * day) }], [], now);
    expect(e).toMatchObject({ hasAccess: true, status: "trialing", reason: "trial" });
    expect(e.accessUntil).toBe(new Date(now.getTime() + 3 * day).toISOString());
  });

  it("cancel during trial is a scheduled cancel with access until effective date", () => {
    const eff = new Date(now.getTime() + 3 * day);
    const e = computeEntitlement(
      [{ ...base, status: "trialing", trial_end: eff, scheduled_change_action: "cancel", scheduled_change_effective_at: eff }],
      [],
      now,
    );
    expect(e).toMatchObject({ hasAccess: true, status: "scheduled_cancel" });
    expect(e.accessUntil).toBe(eff.toISOString());
  });

  it("active with scheduled cancel keeps access, displayed as scheduled_cancel", () => {
    const e = computeEntitlement([{ ...base, scheduled_change_action: "cancel", scheduled_change_effective_at: base.current_period_end }], [], now);
    expect(e).toMatchObject({ hasAccess: true, status: "scheduled_cancel" });
  });

  it("canceled (effective) → no access", () => {
    expect(computeEntitlement([{ ...base, status: "canceled" }], [], now)).toMatchObject({ hasAccess: false, status: "canceled" });
  });

  it("paused → no access", () => {
    expect(computeEntitlement([{ ...base, status: "paused" }], [], now).hasAccess).toBe(false);
  });

  it(`past_due → grace for ${PAST_DUE_GRACE_DAYS} days then no access`, () => {
    const recent = { ...base, status: "past_due" as const, current_period_start: new Date(now.getTime() - 2 * day) };
    expect(computeEntitlement([recent], [], now)).toMatchObject({ hasAccess: true, reason: "past_due_grace" });
    const old = { ...recent, current_period_start: new Date(now.getTime() - (PAST_DUE_GRACE_DAYS + 1) * day) };
    expect(computeEntitlement([old], [], now)).toMatchObject({ hasAccess: false, reason: "past_due_expired" });
  });

  it("prefers active over an older canceled subscription", () => {
    const e = computeEntitlement([{ ...base, status: "canceled" }, base], [], now);
    expect(e.status).toBe("active");
  });

  it("admin grant gives access only while unexpired and unrevoked", () => {
    const grant = { expires_at: new Date(now.getTime() + day), revoked_at: null };
    expect(computeEntitlement([], [grant], now)).toMatchObject({ hasAccess: true, reason: "admin_grant" });
    expect(computeEntitlement([], [{ ...grant, revoked_at: now }], now).hasAccess).toBe(false);
    expect(computeEntitlement([], [{ ...grant, expires_at: new Date(now.getTime() - 1) }], now).hasAccess).toBe(false);
  });
});
