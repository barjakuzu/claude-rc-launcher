"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { Briefcase, Check, Home, Loader2, Navigation2, Pencil, Search, Star, Trash2, X } from "lucide-react";
import type { Place } from "@/lib/providers/types";

type Saved = { id: string; kind: "home" | "work" | "favorite"; label: string; name: string; address: string | null; lat: number; lon: number };

async function call(url: string, method: string, body?: unknown) {
  const r = await fetch(url, { method, headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined });
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(d.error ?? `HTTP ${r.status}`);
  return d;
}

export function SavedManager({ initial }: { initial: Saved[] }) {
  const router = useRouter();
  const [removed, setRemoved] = useState<Set<string>>(() => new Set());
  const items = initial.filter((x) => !removed.has(x.id));
  const [q, setQ] = useState("");
  const [found, setFound] = useState<{ q: string; places: Place[] }>({ q: "", places: [] });
  const results = q.trim().length >= 2 && found.q === q.trim() ? found.places : [];
  const [kind, setKind] = useState<Saved["kind"]>("favorite");
  const [busy, setBusy] = useState(false);
  const [editing, setEditing] = useState<string | null>(null);
  const [label, setLabel] = useState("");
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    const s = q.trim();
    if (s.length < 2) return;
    const ctrl = new AbortController();
    const t = setTimeout(() => {
      fetch(`/api/geo/search?q=${encodeURIComponent(s)}&lang=${/[Ⴀ-ჿ]/.test(s) ? "ka" : "en"}`, { signal: ctrl.signal })
        .then((r) => r.json())
        .then((d) => setFound({ q: s, places: d.places ?? [] }))
        .catch(() => {});
    }, 350);
    return () => {
      clearTimeout(t);
      ctrl.abort();
    };
  }, [q]);

  const add = async (p: Place) => {
    setBusy(true);
    setErr(null);
    try {
      const label = kind === "home" ? "Home" : kind === "work" ? "Work" : p.name.slice(0, 60);
      await call("/api/places", "POST", { kind, label, name: p.name, address: p.address, lat: p.lat, lon: p.lon, source: p.source });
      setQ("");
      router.refresh();
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="grid gap-5 lg:grid-cols-[1fr_380px]">
      <section aria-label="Your places" className="grid h-fit gap-2">
        {items.length === 0 && <p className="glass p-5 text-muted">No saved places yet. Add Home and Work for one-tap routes.</p>}
        {items.map((p) => {
          const I = p.kind === "home" ? Home : p.kind === "work" ? Briefcase : Star;
          return (
            <article key={p.id} className="glass flex items-center gap-3 p-4">
              <I className="h-6 w-6 shrink-0 text-accent-2" aria-hidden />
              <div className="min-w-0 flex-1">
                {editing === p.id ? (
                  <form
                    className="flex gap-2"
                    onSubmit={async (e) => {
                      e.preventDefault();
                      try {
                        await call(`/api/places/${p.id}`, "PATCH", { label });
                        setEditing(null);
                        router.refresh();
                      } catch (er) {
                        setErr((er as Error).message);
                      }
                    }}
                  >
                    <input className="field !min-h-10" value={label} onChange={(e) => setLabel(e.target.value)} maxLength={60} aria-label="Label" autoFocus />
                    <button className="btn btn-primary !min-h-10 !px-3" aria-label="Save label"><Check className="h-4 w-4" /></button>
                    <button type="button" className="btn btn-ghost !min-h-10 !px-3" aria-label="Cancel" onClick={() => setEditing(null)}><X className="h-4 w-4" /></button>
                  </form>
                ) : (
                  <>
                    <p className="truncate font-semibold">{p.label}</p>
                    <p className="truncate text-sm text-muted">{p.name}{p.address ? ` · ${p.address}` : ""}</p>
                  </>
                )}
              </div>
              <Link href={`/app/map?lat=${p.lat}&lon=${p.lon}&name=${encodeURIComponent(p.name)}`} className="btn btn-ghost !min-h-11 !px-3" aria-label={`Route to ${p.label}`}>
                <Navigation2 className="h-4 w-4" />
              </Link>
              <button className="btn btn-ghost !min-h-11 !px-3" aria-label={`Rename ${p.label}`} onClick={() => { setEditing(p.id); setLabel(p.label); }}>
                <Pencil className="h-4 w-4" />
              </button>
              <button
                className="btn btn-danger !min-h-11 !px-3"
                aria-label={`Delete ${p.label}`}
                onClick={async () => {
                  if (!confirm(`Delete “${p.label}”?`)) return;
                  try {
                    await call(`/api/places/${p.id}`, "DELETE");
                    setRemoved((r) => new Set(r).add(p.id));
                  } catch (e) {
                    setErr((e as Error).message);
                  }
                }}
              >
                <Trash2 className="h-4 w-4" />
              </button>
            </article>
          );
        })}
        {err && <p role="alert" className="text-sm text-bad">{err}</p>}
      </section>

      <section className="glass h-fit p-5" aria-label="Add a place">
        <h2 className="text-lg font-semibold">Add a place</h2>
        <div className="mt-3 grid grid-cols-3 gap-2" role="radiogroup" aria-label="Type">
          {([["home", "Home", Home], ["work", "Work", Briefcase], ["favorite", "Favourite", Star]] as const).map(([k, l, I]) => (
            <button key={k} role="radio" aria-checked={kind === k} onClick={() => setKind(k)} className={`flex flex-col items-center gap-1 rounded-xl px-2 py-3 text-sm font-semibold ${kind === k ? "bg-accent text-white" : "bg-white/5"}`}>
              <I className="h-5 w-5" aria-hidden /> {l}
            </button>
          ))}
        </div>
        <label className="mt-4 flex items-center gap-2 rounded-xl border border-line-strong px-3">
          <Search className="h-5 w-5 text-muted" aria-hidden />
          <input className="h-12 flex-1 bg-transparent outline-none" placeholder="Search address or place" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search address or place" />
          {busy && <Loader2 className="h-4 w-4 animate-spin" aria-hidden />}
        </label>
        <ul className="mt-2 grid gap-1">
          {results.map((r) => (
            <li key={r.id}>
              <button onClick={() => add(r)} disabled={busy} className="w-full rounded-xl px-3 py-2.5 text-left hover:bg-white/5">
                <span className="block truncate font-medium">{r.name}</span>
                <span className="block truncate text-xs text-muted">{r.address}</span>
              </button>
            </li>
          ))}
        </ul>
        <p className="mt-3 text-xs text-faint">Saved places are private to your account. Delete them anytime.</p>
      </section>
    </div>
  );
}
