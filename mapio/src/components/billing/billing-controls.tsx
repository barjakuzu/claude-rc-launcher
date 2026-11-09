"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { ExternalLink, Loader2, RotateCcw } from "lucide-react";

async function post<T>(url: string): Promise<T> {
  const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error((d as { error?: string }).error ?? `HTTP ${r.status}`);
  return d as T;
}

type CheckoutResp =
  | { kind: "paddle"; transactionId: string; clientToken: string; environment: "sandbox" | "production" }
  | { kind: "dev"; url: string };

/**
 * Starts checkout. Paddle: opens the overlay for a server-created transaction.
 * Access is granted only after the verified webhook arrives — the success page polls for it.
 */
export function CheckoutButton({ label, className = "" }: { label: string; className?: string }) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  return (
    <div className="grid gap-2">
      <button
        className={`btn btn-primary !min-h-14 text-lg ${className}`}
        disabled={busy}
        onClick={async () => {
          setBusy(true);
          setErr(null);
          try {
            const r = await post<CheckoutResp>("/api/billing/checkout");
            if (r.kind === "dev") {
              router.push(r.url);
              return;
            }
            const { initializePaddle } = await import("@paddle/paddle-js");
            const paddle = await initializePaddle({ environment: r.environment, token: r.clientToken });
            if (!paddle) throw new Error("Could not load the payment form. Check your connection or ad blocker.");
            paddle.Checkout.open({
              transactionId: r.transactionId,
              settings: {
                displayMode: "overlay",
                theme: document.documentElement.dataset.theme === "light" ? "light" : "dark",
                successUrl: `${location.origin}/account/billing?checkout=complete`,
              },
            });
          } catch (e) {
            setErr((e as Error).message);
          } finally {
            setBusy(false);
          }
        }}
      >
        {busy && <Loader2 className="h-5 w-5 animate-spin" aria-hidden />} {label}
      </button>
      {err && <p role="alert" className="text-sm text-bad">{err}</p>}
    </div>
  );
}

export function PortalButton({ label = "Manage payment & invoices" }: { label?: string }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  return (
    <div className="grid gap-1">
      <button
        className="btn btn-ghost"
        disabled={busy}
        onClick={async () => {
          setBusy(true);
          setErr(null);
          try {
            const { url } = await post<{ url: string }>("/api/billing/portal");
            window.location.href = url;
          } catch (e) {
            setErr((e as Error).message);
            setBusy(false);
          }
        }}
      >
        {busy ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : <ExternalLink className="h-4 w-4" aria-hidden />} {label}
      </button>
      {err && <p role="alert" className="text-sm text-bad">{err}</p>}
    </div>
  );
}

export function CancelControls({ scheduled, accessUntilLabel }: { scheduled: boolean; accessUntilLabel: string }) {
  const router = useRouter();
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const run = async (url: string) => {
    setBusy(true);
    setErr(null);
    try {
      await post(url);
      setConfirming(false);
      router.refresh();
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  if (scheduled) {
    return (
      <div className="grid gap-1">
        <button className="btn btn-ghost" disabled={busy} onClick={() => run("/api/billing/undo-cancel")}>
          {busy ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : <RotateCcw className="h-4 w-4" aria-hidden />} Keep my subscription
        </button>
        {err && <p role="alert" className="text-sm text-bad">{err}</p>}
      </div>
    );
  }
  if (!confirming) {
    return (
      <button className="btn btn-danger" onClick={() => setConfirming(true)}>
        Cancel subscription
      </button>
    );
  }
  return (
    <div role="alertdialog" aria-labelledby="cancel-title" className="rounded-2xl border border-bad/40 bg-bad/10 p-4">
      <p id="cancel-title" className="font-semibold">Cancel MAPIO Premium?</p>
      <p className="mt-1 text-sm text-muted">
        You keep access until <b className="text-text">{accessUntilLabel}</b>. You won&apos;t be charged again, and you can undo this before then.
      </p>
      <div className="mt-3 flex flex-wrap gap-2">
        <button className="btn btn-danger" disabled={busy} onClick={() => run("/api/billing/cancel")}>
          {busy && <Loader2 className="h-4 w-4 animate-spin" aria-hidden />} Yes, cancel at period end
        </button>
        <button className="btn btn-ghost" onClick={() => setConfirming(false)}>Keep subscription</button>
      </div>
      {err && <p role="alert" className="mt-2 text-sm text-bad">{err}</p>}
    </div>
  );
}

/** After Paddle redirects back, wait for the webhook-driven entitlement instead of trusting the redirect. */
export function AwaitEntitlement() {
  const router = useRouter();
  const [tries, setTries] = useState(0);
  useEffect(() => {
    if (tries >= 20) return;
    let cancelled = false;
    const t = setTimeout(async () => {
      const r = await fetch("/api/billing/entitlement", { cache: "no-store" }).then((x) => x.json()).catch(() => null);
      if (cancelled) return;
      if (r?.entitlement?.hasAccess) {
        router.replace("/account/billing?checkout=activated");
        router.refresh();
      } else setTries((n) => n + 1);
    }, 1500);
    return () => {
      cancelled = true;
      clearTimeout(t);
    };
  }, [tries, router]);
  return (
    <p role="status" className="flex items-center gap-2 rounded-2xl border border-line bg-white/5 px-4 py-3 text-sm">
      {tries < 20 ? (
        <>
          <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> Confirming your subscription with our payment provider…
        </>
      ) : (
        "Still waiting for payment confirmation. This can take a minute — refresh this page shortly."
      )}
    </p>
  );
}
