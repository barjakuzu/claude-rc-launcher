"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { Car, CheckCircle2, Loader2, Pencil, QrCode, Smartphone, Trash2 } from "lucide-react";
import { relativeTime } from "@/lib/format";

type Device = { id: string; name: string; device_type: string; user_agent: string | null; created_at: string; last_seen_at: string | null; revoked_at: string | null; online: boolean };

async function call<T>(url: string, method = "POST", body?: unknown): Promise<T> {
  const r = await fetch(url, { method, headers: { "Content-Type": "application/json" }, body: body !== undefined ? JSON.stringify(body) : method === "GET" ? undefined : "{}" });
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error((d as { error?: string }).error ?? `HTTP ${r.status}`);
  return d as T;
}

/** Pair THIS browser: show a one-time QR, poll until the phone approves, then redeem. */
export function PairThisBrowser({ alreadyPaired }: { alreadyPaired: boolean }) {
  const router = useRouter();
  const [name, setName] = useState("Car browser");
  const [req, setReq] = useState<{ requestId: string; approveUrl: string; expiresAt: string } | null>(null);
  const [qr, setQr] = useState<string | null>(null);
  const [status, setStatus] = useState<"idle" | "pending" | "approved" | "done" | "expired" | "error">("idle");
  const [err, setErr] = useState<string | null>(null);
  const [left, setLeft] = useState(0);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);

  const stop = () => {
    if (timer.current) clearInterval(timer.current);
  };
  useEffect(() => stop, []);

  const start = useCallback(async () => {
    setErr(null);
    setStatus("pending");
    try {
      const r = await call<{ requestId: string; approveUrl: string; expiresAt: string }>("/api/devices/pairing", "POST", { name });
      setReq(r);
      const QR = await import("qrcode");
      setQr(await QR.toDataURL(r.approveUrl, { margin: 1, width: 320, color: { dark: "#06122a", light: "#ffffff" } }));
      stop();
      timer.current = setInterval(async () => {
        const remaining = Math.max(0, Math.round((new Date(r.expiresAt).getTime() - Date.now()) / 1000));
        setLeft(remaining);
        try {
          const s = await call<{ status: string }>(`/api/devices/pairing/${r.requestId}`, "GET");
          if (s.status === "approved") {
            stop();
            setStatus("approved");
            await call(`/api/devices/pairing/${r.requestId}/redeem`);
            setStatus("done");
            router.refresh();
          } else if (s.status === "expired" || s.status === "not_found") {
            stop();
            setStatus("expired");
          }
        } catch (e) {
          stop();
          setErr((e as Error).message);
          setStatus("error");
        }
      }, 2000);
    } catch (e) {
      setErr((e as Error).message);
      setStatus("error");
    }
  }, [name, router]);

  if (status === "done") {
    return (
      <p role="status" className="flex items-center gap-2 rounded-2xl border border-ok/40 bg-ok/10 p-4 text-ok">
        <CheckCircle2 className="h-5 w-5" aria-hidden /> This browser is paired. Destinations sent from your phone will appear here.
      </p>
    );
  }

  return (
    <div className="grid gap-4 md:grid-cols-[1fr_auto] md:items-center">
      <div>
        <p className="text-muted">
          {alreadyPaired
            ? "This browser is already paired. You can pair it again to replace its credential."
            : "Open this page in your car's browser, then scan the QR code with your phone while signed in to the same MAPIO account."}
        </p>
        {status === "idle" || status === "expired" || status === "error" ? (
          <div className="mt-4 flex flex-wrap items-end gap-2">
            <label className="grid flex-1 gap-1 text-sm font-medium">
              Device name
              <input className="field" value={name} maxLength={60} onChange={(e) => setName(e.target.value)} />
            </label>
            <button className="btn btn-primary" onClick={start}>
              <QrCode className="h-5 w-5" aria-hidden /> {status === "expired" ? "New code" : "Pair this browser"}
            </button>
          </div>
        ) : (
          <p role="status" className="mt-4 flex items-center gap-2 text-sm">
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
            {status === "approved" ? "Approved — finishing pairing…" : `Waiting for approval on your phone… code expires in ${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")}`}
          </p>
        )}
        {status === "expired" && <p className="mt-2 text-sm text-warn">The code expired. Create a new one.</p>}
        {err && <p role="alert" className="mt-2 text-sm text-bad">{err}</p>}
        {req && status === "pending" && (
          <p className="mt-3 break-all text-xs text-faint">
            Can&apos;t scan? Open this one-time link on your phone: <span className="select-all text-muted">{req.approveUrl}</span>
          </p>
        )}
      </div>
      {qr && status === "pending" && (
        // eslint-disable-next-line @next/next/no-img-element
        <img src={qr} alt="Pairing QR code — scan with your phone" width={240} height={240} className="mx-auto rounded-2xl bg-white p-2" />
      )}
    </div>
  );
}

export function DeviceList({ devices, currentId }: { devices: Device[]; currentId: string | null }) {
  const router = useRouter();
  const [editing, setEditing] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [err, setErr] = useState<string | null>(null);
  if (devices.length === 0) return <p className="text-muted">No paired browsers yet.</p>;
  return (
    <ul className="grid gap-2">
      {devices.map((d) => {
        const revoked = !!d.revoked_at;
        const online = !revoked && d.online;
        const I = d.device_type === "phone" ? Smartphone : Car;
        return (
          <li key={d.id} className={`flex flex-wrap items-center gap-3 rounded-2xl border border-line p-4 ${revoked ? "opacity-60" : ""}`}>
            <I className="h-6 w-6 text-accent-2" aria-hidden />
            <div className="min-w-0 flex-1">
              {editing === d.id ? (
                <form
                  className="flex gap-2"
                  onSubmit={async (e) => {
                    e.preventDefault();
                    try {
                      await call(`/api/devices/${d.id}`, "PATCH", { name });
                      setEditing(null);
                      router.refresh();
                    } catch (er) {
                      setErr((er as Error).message);
                    }
                  }}
                >
                  <input className="field !min-h-10" value={name} onChange={(e) => setName(e.target.value)} maxLength={60} aria-label="Device name" autoFocus />
                  <button className="btn btn-primary !min-h-10">Save</button>
                </form>
              ) : (
                <p className="font-semibold">
                  {d.name} {d.id === currentId && <span className="chip ml-2 text-xs text-accent-2">This browser</span>}
                </p>
              )}
              <p className="text-sm text-muted">
                {revoked ? `Revoked ${relativeTime(d.revoked_at!)}` : online ? "Online" : d.last_seen_at ? `Last seen ${relativeTime(d.last_seen_at)}` : "Never seen"}
                {" · paired "}
                {relativeTime(d.created_at)}
              </p>
            </div>
            {!revoked && (
              <div className="flex gap-2">
                <button className="btn btn-ghost !min-h-11 !px-3" aria-label={`Rename ${d.name}`} onClick={() => { setEditing(d.id); setName(d.name); }}>
                  <Pencil className="h-4 w-4" />
                </button>
                <button
                  className="btn btn-danger !min-h-11"
                  onClick={async () => {
                    if (!confirm(`Revoke “${d.name}”? It will stop receiving destinations.`)) return;
                    try {
                      await call(`/api/devices/${d.id}`, "DELETE");
                      router.refresh();
                    } catch (e) {
                      setErr((e as Error).message);
                    }
                  }}
                >
                  <Trash2 className="h-4 w-4" aria-hidden /> Revoke
                </button>
              </div>
            )}
          </li>
        );
      })}
      {err && <p role="alert" className="text-sm text-bad">{err}</p>}
    </ul>
  );
}
