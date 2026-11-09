import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { AuthCard } from "@/components/auth/auth-card";
import { DevSimulator } from "@/components/billing/dev-simulator";
import { requireUser } from "@/lib/auth";
import { billingMode } from "@/lib/env";
import { getBillingState } from "@/lib/billing";

export const dynamic = "force-dynamic";
export const metadata: Metadata = { title: "Billing simulator portal", robots: { index: false } };

export default async function DevPortalPage() {
  let mode: string;
  try { mode = billingMode(); } catch { notFound(); }
  if (mode !== "demo") notFound();
  const user = await requireUser("/dev/billing/portal");
  const s = await getBillingState(user.id, user.email);
  return (
    <AuthCard title="Development customer portal" subtitle="Stands in for the Paddle customer portal. Every action emits a signed simulated webhook.">
      <p className="mb-4 text-sm text-muted">Current status: <b className="text-text">{s.entitlement.status}</b></p>
      <DevSimulator mode="portal" />
    </AuthCard>
  );
}
