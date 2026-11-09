"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { Loader2, LocateFixed, MapPinned, Navigation2, RefreshCw, Zap } from "lucide-react";
import type { Charger, LngLat, ProviderMeta } from "@/lib/providers/types";
import { formatDistance, relativeTime, type Units } from "@/lib/format";

/** List + filter view of chargers. "Show on map" opens the full map with the charger layer. */
export function ChargerBrowser({ center, units }: { center: LngLat; units: Units }) {
  const [origin, setOrigin] = useState<LngLat>(center);
  const [originLabel, setOriginLabel] = useState("Central Tbilisi");
  const [current, setCurrent] = useState<"" | "AC" | "DC">("");
  const [minKw, setMinKw] = useState(0);
  const [radius, setRadius] = useState(10);
  const [nonce, setNonce] = useState(0);
  const key = `${origin.join(",")}|${current}|${minKw}|${radius}|${nonce}`;
  const [res, setRes] = useState<{ key: string; list: Charger[]; meta: ProviderMeta | null; err: string | null }>({ key: "", list: [], meta: null, err: null });
  const [geoErr, setGeoErr] = useState<string | null>(null);
  const loading = res.key !== key;
  const { list, meta } = res;
  const err = geoErr ?? res.err;

  useEffect(() => {
    const ctrl = new AbortController();
    const p = new URLSearchParams({ lat: String(origin[1]), lon: String(origin[0]), radius: String(radius) });
    if (current) p.set("current", current);
    if (minKw) p.set("minKw", String(minKw));
    fetch(`/api/chargers?${p}`, { signal: ctrl.signal })
      .then(async (r) => {
        const d = await r.json();
        if (!r.ok) throw new Error(d.error ?? "Failed to load chargers");
        setRes({ key, list: d.chargers, meta: d.meta, err: null });
      })
      .catch((e) => {
        if ((e as Error).name !== "AbortError") setRes({ key, list: [], meta: null, err: (e as Error).message });
      });
    return () => ctrl.abort();
  }, [key, origin, current, minKw, radius]);
  const load = useCallback(() => setNonce((n) => n + 1), []);

  const useMyLocation = () => {
    navigator.geolocation?.getCurrentPosition(
      (pos) => {
        setOrigin([pos.coords.longitude, pos.coords.latitude]);
        setOriginLabel("Your location");
      },
      () => setGeoErr("Location unavailable — showing results for central Tbilisi."),
      { enableHighAccuracy: false, timeout: 10_000 },
    );
  };

  return (
    <div className="grid gap-5 lg:grid-cols-[320px_1fr]">
      <section className="glass h-fit p-5" aria-label="Filters">
        <p className="text-sm text-muted">Near</p>
        <p className="font-semibold">{originLabel}</p>
        <button className="btn btn-ghost mt-3 w-full" onClick={useMyLocation}>
          <LocateFixed className="h-4 w-4" aria-hidden /> Use my location
        </button>
        <fieldset className="mt-5">
          <legend className="mb-2 text-sm font-semibold">Current</legend>
          <div className="grid grid-cols-3 gap-2">
            {([["", "Any"], ["AC", "AC"], ["DC", "DC fast"]] as const).map(([v, l]) => (
              <button key={l} aria-pressed={current === v} onClick={() => setCurrent(v)} className={`rounded-xl px-2 py-3 text-sm font-semibold ${current === v ? "bg-accent text-white" : "bg-white/5"}`}>
                {l}
              </button>
            ))}
          </div>
        </fieldset>
        <label className="mt-5 block text-sm font-semibold">
          Minimum power: {minKw ? `${minKw} kW` : "any"}
          <input type="range" min={0} max={250} step={25} value={minKw} onChange={(e) => setMinKw(Number(e.target.value))} className="mt-2 w-full accent-[var(--accent)]" />
        </label>
        <label className="mt-4 block text-sm font-semibold">
          Radius: {radius} km
          <input type="range" min={2} max={50} step={1} value={radius} onChange={(e) => setRadius(Number(e.target.value))} className="mt-2 w-full accent-[var(--accent)]" />
        </label>
        <Link href={`/app/map?chargers=1`} className="btn btn-primary mt-5 w-full">
          <MapPinned className="h-4 w-4" aria-hidden /> Show on map
        </Link>
      </section>

      <section aria-label="Results" aria-busy={loading}>
        <div className="mb-3 flex items-center justify-between gap-3">
          <p className="text-muted">{loading ? "Loading…" : `${list.length} location${list.length === 1 ? "" : "s"}`}</p>
          <button className="btn btn-ghost !min-h-10" onClick={() => load()} aria-label="Refresh">
            {loading ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : <RefreshCw className="h-4 w-4" aria-hidden />} Refresh
          </button>
        </div>
        {meta?.notice && (
          <p className={`mb-3 rounded-xl px-4 py-3 text-sm ${meta.mode === "demo" ? "border border-warn/50 bg-warn/10 text-warn" : "border border-line bg-white/5 text-muted"}`}>{meta.notice}</p>
        )}
        {err && <p role="alert" className="mb-3 rounded-xl border border-bad/50 bg-bad/10 px-4 py-3 text-sm text-bad">{err}</p>}
        <ul className="grid gap-3 xl:grid-cols-2">
          {list.map((c) => (
            <li key={c.id} className="glass p-4">
              <div className="flex items-start gap-3">
                <span className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-[#7c6cff]/20 text-[#b3a8ff]">
                  <Zap className="h-5 w-5" aria-hidden />
                </span>
                <div className="min-w-0 flex-1">
                  <h3 className="truncate font-semibold">{c.title}</h3>
                  <p className="truncate text-sm text-muted">
                    {c.address || "Address not provided"}
                    {c.distanceKm != null && ` · ${formatDistance(c.distanceKm * 1000, units.distance)}`}
                  </p>
                </div>
              </div>
              <p className="mt-3 flex flex-wrap gap-1.5">
                {c.connectors.map((k, i) => (
                  <span key={i} className="chip text-xs">
                    {k.type} · {k.current}
                    {k.powerKw != null ? ` · ${k.powerKw} kW` : ""}
                  </span>
                ))}
              </p>
              <dl className="mt-3 grid grid-cols-2 gap-1 text-xs">
                <dt className="text-muted">Live availability</dt>
                <dd className="font-semibold text-warn">Unknown</dd>
                <dt className="text-muted">Reported status</dt>
                <dd>
                  {c.reportedStatus ?? "Not reported"}
                  {c.statusUpdatedAt && ` · ${relativeTime(c.statusUpdatedAt)}`}
                </dd>
                <dt className="text-muted">Source</dt>
                <dd className="truncate">{[c.dataProvider, c.license].filter(Boolean).join(" · ") || "—"}</dd>
              </dl>
              <Link href={`/app/map?lat=${c.lat}&lon=${c.lon}&name=${encodeURIComponent(c.title)}`} className="btn btn-primary mt-3 w-full !min-h-11 text-sm">
                <Navigation2 className="h-4 w-4" aria-hidden /> Route here
              </Link>
            </li>
          ))}
        </ul>
        {meta && <p className="mt-4 text-xs text-faint">{meta.attribution} · updated {relativeTime(meta.fetchedAt)}</p>}
      </section>
    </div>
  );
}
