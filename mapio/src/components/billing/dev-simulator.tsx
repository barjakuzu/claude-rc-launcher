"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { Loader2 } from "lucide-react";

const PORTAL_ACTIONS: [string, string][] = [
  ["convert_trial", "End trial: first payment succeeds"],
  ["expire_trial_unpaid", "End trial: payment fails → canceled"],
  ["fail_payment", "Renewal payment fails (past due)"],
  ["recover_payment", "Payment recovered"],
  ["schedule_cancel", "Cancel at period end"],
  ["undo_cancel", "Undo scheduled cancel"],
  ["cancel_now", "Cancellation takes effect now"],
];

export function DevSimulator({ mode }: { mode: "checkout" | "portal" }) {
  const router = useRouter();
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const run = async (action: string) => {
    setBusy(action);
    setMsg(null);
    const r = await fetch("/api/dev/billing", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action }) });
    const d = await r.json().catch(() => ({}));
    setBusy(null);
    if (!r.ok) return setMsg(d.error ?? "Failed");
    if (mode === "checkout") router.push("/account/billing?checkout=complete");
    else {
      setMsg(`Webhook ${d.outcome}. Status now: ${d.state?.entitlement?.status}`);
      router.refresh();
    }
  };
  if (mode === "checkout") {
    return (
      <button className="btn btn-primary w-full !min-h-14 text-lg" disabled={!!busy} onClick={() => run("start_trial")}>
        {busy && <Loader2 className="h-5 w-5 animate-spin" aria-hidden />} Simulate successful checkout
      </button>
    );
  }
  return (
    <div className="grid gap-2">
      {PORTAL_ACTIONS.map(([a, l]) => (
        <button key={a} className="btn btn-ghost justify-start" disabled={!!busy} onClick={() => run(a)}>
          {busy === a && <Loader2 className="h-4 w-4 animate-spin" aria-hidden />} {l}
        </button>
      ))}
      {msg && <p role="status" className="text-sm text-muted">{msg}</p>}
      <a href="/account/billing" className="mt-2 text-center text-sm text-accent-2 underline">Back to billing</a>
    </div>
  );
}
