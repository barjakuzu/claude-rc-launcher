/**
 * Pure entitlement rules — no I/O, fully unit tested (tests/unit/entitlement.test.ts).
 * Inputs are rows written ONLY by verified webhooks / reconciliation / audited admin grants.
 */
export type ProviderStatus = "trialing" | "active" | "past_due" | "paused" | "canceled";
export type DisplayStatus = ProviderStatus | "scheduled_cancel" | "none";

export interface SubscriptionRow {
  status: ProviderStatus;
  trial_end: Date | string | null;
  current_period_start: Date | string | null;
  current_period_end: Date | string | null;
  next_billed_at: Date | string | null;
  scheduled_change_action: "cancel" | "pause" | "resume" | null;
  scheduled_change_effective_at: Date | string | null;
  updated_at?: Date | string | null;
}
export interface GrantRow {
  expires_at: Date | string;
  revoked_at: Date | string | null;
}

/** Business rule: keep access for this long while Paddle retries a failed renewal. */
export const PAST_DUE_GRACE_DAYS = 7;

const t = (d: Date | string | null | undefined) => (d ? new Date(d).getTime() : null);

export interface Entitlement {
  hasAccess: boolean;
  status: DisplayStatus;
  reason:
    | "trial"
    | "active"
    | "past_due_grace"
    | "past_due_expired"
    | "paused"
    | "canceled"
    | "admin_grant"
    | "no_subscription";
  accessUntil: string | null;
}

export function computeEntitlement(
  subs: SubscriptionRow[],
  grants: GrantRow[] = [],
  now: Date = new Date(),
): Entitlement {
  const n = now.getTime();
  // Prefer the "best" subscription if a user somehow has several.
  const rank: Record<ProviderStatus, number> = { active: 5, trialing: 4, past_due: 3, paused: 2, canceled: 1 };
  const sub = [...subs].sort(
    (a, b) => rank[b.status] - rank[a.status] || (t(b.updated_at) ?? 0) - (t(a.updated_at) ?? 0),
  )[0];

  const grant = grants
    .filter((g) => !g.revoked_at && (t(g.expires_at) ?? 0) > n)
    .sort((a, b) => (t(b.expires_at) ?? 0) - (t(a.expires_at) ?? 0))[0];

  const fromSub = sub ? subEntitlement(sub, n) : null;
  if (fromSub?.hasAccess) return fromSub;
  if (grant) {
    return {
      hasAccess: true,
      status: fromSub?.status ?? "none",
      reason: "admin_grant",
      accessUntil: new Date(grant.expires_at).toISOString(),
    };
  }
  return fromSub ?? { hasAccess: false, status: "none", reason: "no_subscription", accessUntil: null };
}

function subEntitlement(sub: SubscriptionRow, n: number): Entitlement {
  const scheduledCancel = sub.scheduled_change_action === "cancel";
  const iso = (v: number | null) => (v == null ? null : new Date(v).toISOString());
  switch (sub.status) {
    case "trialing": {
      // Paddle keeps "trialing" until the first charge succeeds; the trial end is the
      // authoritative boundary. If a webhook is late we still honour the provider state.
      const until = t(sub.trial_end) ?? t(sub.current_period_end);
      return {
        hasAccess: true,
        status: scheduledCancel ? "scheduled_cancel" : "trialing",
        reason: "trial",
        accessUntil: iso(scheduledCancel ? (t(sub.scheduled_change_effective_at) ?? until) : until),
      };
    }
    case "active":
      return {
        hasAccess: true,
        status: scheduledCancel ? "scheduled_cancel" : "active",
        reason: "active",
        accessUntil: iso(
          scheduledCancel ? (t(sub.scheduled_change_effective_at) ?? t(sub.current_period_end)) : t(sub.current_period_end),
        ),
      };
    case "past_due": {
      const failedAt = t(sub.current_period_start) ?? t(sub.next_billed_at) ?? n;
      const graceEnd = failedAt + PAST_DUE_GRACE_DAYS * 86_400_000;
      const inGrace = n < graceEnd;
      return {
        hasAccess: inGrace,
        status: "past_due",
        reason: inGrace ? "past_due_grace" : "past_due_expired",
        accessUntil: iso(graceEnd),
      };
    }
    case "paused":
      return { hasAccess: false, status: "paused", reason: "paused", accessUntil: null };
    case "canceled":
      return { hasAccess: false, status: "canceled", reason: "canceled", accessUntil: null };
  }
}
