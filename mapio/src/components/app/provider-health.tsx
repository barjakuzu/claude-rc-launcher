"use client";

import { useState } from "react";
import { Activity, Loader2 } from "lucide-react";

type Check = { name: string; mode: string; ok: boolean; ms: number; detail: string };

export function ProviderHealthPanel() {
  const [checks, setChecks] = useState<Check[] | null>(null);
  const [busy, setBusy] = useState(false);
  return (
    <div>
      <button
        className="btn btn-ghost !min-h-10"
        disabled={busy}
        onClick={async () => {
          setBusy(true);
          const r = await fetch("/api/admin/health").then((x) => x.json()).catch(() => null);
          setChecks(r?.checks ?? []);
          setBusy(false);
        }}
      >
        {busy ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : <Activity className="h-4 w-4" aria-hidden />} Run provider health checks
      </button>
      {checks && (
        <ul className="mt-3 grid gap-1 text-sm">
          {checks.map((c) => (
            <li key={c.name} className="flex gap-2">
              <span className={c.ok ? "text-ok" : "text-bad"}>{c.ok ? "OK" : "FAIL"}</span>
              <span className="font-semibold">{c.name}</span>
              <span className="text-muted">[{c.mode}] {c.ms} ms — {c.detail}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
