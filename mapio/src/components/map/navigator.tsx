"use client";

import dynamic from "next/dynamic";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowLeft,
  Box,
  Car,
  ChevronRight,
  CloudSun,
  CornerUpLeft,
  CornerUpRight,
  Inbox,
  Layers3,
  Loader2,
  LocateFixed,
  MapPin,
  MoveUp,
  Navigation2,
  Search,
  Send,
  Square,
  Star,
  Volume2,
  VolumeX,
  WifiOff,
  X,
  Zap,
} from "lucide-react";
import type { MapViewHandle } from "./map-view";
import { useDebounced, useGeolocation, useInbox, type InboxItemDTO } from "./hooks";
import type { MapClientConfig } from "@/lib/map/config";
import type { Charger, LngLat, Place, ProviderMeta, Route, Weather } from "@/lib/providers/types";
import { distM, isOffRoute, prepareRoute, progress, type PreparedRoute } from "@/lib/nav";
import { formatClock, formatDistance, formatDuration, formatTemp, relativeTime, type Units } from "@/lib/format";
import type { DictKey } from "@/lib/i18n";
import { setDomTheme, setLowPerfMode, useDomTheme, useLocalJson, useLocalPref, useLowPerf, useNow } from "@/components/hooks/client-prefs";

const MapView = dynamic(() => import("./map-view").then((m) => m.MapView), {
  ssr: false,
  loading: () => (
    <div role="status" className="absolute inset-0 grid place-items-center bg-[radial-gradient(60%_60%_at_50%_45%,#10264a,#060f22)]">
      <span className="glass-strong flex items-center gap-3 !rounded-full px-5 py-3 text-sm text-muted">
        <Loader2 className="h-4 w-4 animate-spin text-accent-2" aria-hidden /> Loading map…
      </span>
    </div>
  ),
});

export interface NavigatorProps {
  variant: "drive" | "app";
  config: MapClientConfig;
  locale: "en" | "ka";
  units: Units;
  t: Record<DictKey, string>;
  initialCenter: LngLat;
  devices: { id: string; name: string }[];
  flags: { chargers: boolean; weather: boolean; send_to_car: boolean };
  initialDestination?: { name: string; address: string; lat: number; lon: number; inboxId?: string } | null;
  initialChargers?: boolean;
  focusSearch?: boolean;
}

type Selected = { kind: "place"; place: Place } | { kind: "charger"; charger: Charger } | null;

async function api<T>(url: string, init?: RequestInit): Promise<T> {
  const r = await fetch(url, { cache: "no-store", ...init, headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) } });
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error((d as { error?: string }).error ?? `HTTP ${r.status}`);
  return d as T;
}

function ManeuverIcon({ maneuver, className, strokeWidth }: { maneuver: string | null; className?: string; strokeWidth?: number }) {
  const s = (maneuver ?? "").toLowerCase();
  const props = { className, strokeWidth, "aria-hidden": true } as const;
  if (s.includes("left")) return <CornerUpLeft {...props} />;
  if (s.includes("right")) return <CornerUpRight {...props} />;
  if (s.includes("finish") || s.includes("arrive")) return <MapPin {...props} />;
  return <MoveUp {...props} />;
}

export function Navigator(props: NavigatorProps) {
  const { variant, config, locale, units, t } = props;
  const drive = variant === "drive";
  const map = useRef<MapViewHandle>(null);
  const theme = useDomTheme();
  const lowPerf = useLowPerf();
  const [pref3D, setPref3D] = useLocalPref("mapio_3d", "on");
  const is3D = pref3D !== "off" && !lowPerf;
  const [voicePref, setVoicePref] = useLocalPref("mapio_voice", "off");
  const muted = voicePref !== "on";
  const now = useNow(15_000);
  const [layersOpen, setLayersOpen] = useState(false);
  const [mapBroken, setMapBroken] = useState(false);
  const [tilesDown, setTilesDown] = useState(false);
  const searchInput = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (props.focusSearch) searchInput.current?.focus();
  }, [props.focusSearch]);

  // search
  const [q, setQ] = useState("");
  const dq = useDebounced(q.trim(), 350);
  const [searchRes, setSearchRes] = useState<{ q: string; places: Place[]; meta: ProviderMeta | null }>({ q: "", places: [], meta: null });
  const searching = dq.length >= 2 && searchRes.q !== dq;
  const results = dq.length >= 2 ? searchRes.places : [];
  const searchMeta = searchRes.meta;
  const [searchOpen, setSearchOpen] = useState(false);
  const [recentSearches, setRecentSearches] = useLocalJson<Place[]>("mapio_recent_searches", []);

  // selection / route
  const [selected, setSelected] = useState<Selected>(null);
  const [route, setRoute] = useState<{ route: Route; meta: ProviderMeta; dest: Place; originLabel: string } | null>(null);
  const [routing, setRouting] = useState(false);
  const [navigating, setNavigating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const offsets = useRef<number[]>([]);
  const lastReroute = useRef(0);
  const lastSpoken = useRef<string>("");

  // overlays
  const [chargersOn, setChargersOn] = useState(!!props.initialChargers && props.flags.chargers);
  const [chargers, setChargers] = useState<Charger[]>([]);
  const [chargerMeta, setChargerMeta] = useState<ProviderMeta | null>(null);
  const [chargerFilter, setChargerFilter] = useState<"all" | "AC" | "DC">("all");
  const [weather, setWeather] = useState<{ weather: Weather; meta: ProviderMeta } | null>(null);
  const [inboxOpen, setInboxOpen] = useState(false);
  const [sendState, setSendState] = useState<string | null>(null);

  const geo = useGeolocation();
  const inbox = useInbox(props.flags.send_to_car);
  const [follow, setFollow] = useState(false);


  const flash = useCallback((m: string) => {
    setToast(m);
    setTimeout(() => setToast((x) => (x === m ? null : x)), 4000);
  }, []);

  /* ── search (results keyed by query; state only set from async callbacks) ── */
  useEffect(() => {
    if (dq.length < 2) return;
    const ctrl = new AbortController();
    const c = map.current?.getCenter() ?? props.initialCenter;
    const isKa = /[\u10A0-\u10FF]/.test(dq);
    api<{ places: Place[]; meta: ProviderMeta }>(
      `/api/geo/search?q=${encodeURIComponent(dq)}&lang=${isKa ? "ka" : locale}&lat=${c[1].toFixed(3)}&lon=${c[0].toFixed(3)}`,
      { signal: ctrl.signal },
    )
      .then((d) => setSearchRes({ q: dq, places: d.places, meta: d.meta }))
      .catch((e) => {
        if ((e as Error).name === "AbortError") return;
        setSearchRes({ q: dq, places: [], meta: null });
        setError((e as Error).message);
      });
    return () => ctrl.abort();
  }, [dq, locale, props.initialCenter]);

  const recentSearchesRef = useRef(recentSearches);
  useEffect(() => {
    recentSearchesRef.current = recentSearches;
  }, [recentSearches]);

  // Where the map was before we flew to a chosen place: the fallback start when GPS is unavailable.
  const startCandidate = useRef<LngLat | null>(null);

  const choosePlace = useCallback((p: Place) => {
    startCandidate.current = map.current?.getCenter() ?? null;
    setSelected({ kind: "place", place: p });
    setSearchOpen(false);
    setQ("");
    setFollow(false);
    map.current?.setDestination([p.lon, p.lat]);
    map.current?.flyTo([p.lon, p.lat], 16);
    setRecentSearches([p, ...recentSearchesRef.current.filter((x) => x.id !== p.id)].slice(0, 6));
  }, [setRecentSearches]);

  /* ── routing ── */
  const origin = useCallback((): { ll: LngLat; label: string } => {
    if (geo.fix && !geo.stale) return { ll: geo.fix.lngLat, label: t["route.myLocation"] };
    return { ll: startCandidate.current ?? map.current?.getCenter() ?? props.initialCenter, label: t["route.mapCenter"] };
  }, [geo.fix, geo.stale, props.initialCenter, t]);

  const requestRoute = useCallback(
    async (dest: Place, opts: { reroute?: boolean } = {}) => {
      const o = origin();
      if (distM(o.ll, [dest.lon, dest.lat]) < 30) {
        setError(
          geo.fix && !geo.stale
            ? "You're already at this place."
            : "No starting point yet. Allow location access, or close this place, move the map to where you're starting, and choose it again.",
        );
        return;
      }
      setRouting(true);
      setError(null);
      try {
        const d = await api<{ route: Route; meta: ProviderMeta }>("/api/geo/route", {
          method: "POST",
          body: JSON.stringify({ from: { lat: o.ll[1], lon: o.ll[0] }, to: { lat: dest.lat, lon: dest.lon }, lang: locale }),
        });
        setRoute({ route: d.route, meta: d.meta, dest, originLabel: o.label });
        offsets.current = [];
        map.current?.setRoute(d.route.geometry);
        map.current?.setDestination([dest.lon, dest.lat]);
        if (!opts.reroute) {
          map.current?.fitRoute(d.route.geometry, drive ? { top: 140, bottom: 200, left: 440, right: 120 } : { top: 80, bottom: 80, left: 80, right: 80 });
          void api("/api/places/recent", { method: "POST", body: JSON.stringify({ name: dest.name, address: dest.address, lat: dest.lat, lon: dest.lon, source: dest.source }) }).catch(() => {});
        }
        setSelected(null);
      } catch (e) {
        setError((e as Error).message);
      } finally {
        setRouting(false);
      }
    },
    [origin, locale, drive, geo.fix, geo.stale],
  );

  const endRoute = useCallback(() => {
    setRoute(null);
    setNavigating(false);
    map.current?.setRoute(null);
    map.current?.setDestination(null);
  }, []);

  const canGuide = geo.status === "watching" && !geo.stale;

  // Guidance can be requested before GPS is ready; it activates once a fresh fix arrives.
  const startGuidance = useCallback(() => {
    if (!canGuide) geo.start();
    setNavigating(true);
    setFollow(true);
  }, [canGuide, geo]);

  /* ── live position → puck, follow, progress, reroute ── */
  const prepared = useMemo<PreparedRoute | null>(() => (route ? prepareRoute(route.route) : null), [route]);
  const prog = useMemo(() => (prepared && geo.fix ? progress(prepared, geo.fix.lngLat) : null), [prepared, geo.fix]);
  const arrived = navigating && canGuide && !!prog?.arrived;
  const guiding = navigating && !arrived;
  const waitingForGps = guiding && !canGuide;

  useEffect(() => {
    map.current?.setPuck(geo.fix?.lngLat ?? null, geo.fix?.heading ?? null, geo.stale);
    if (follow && geo.fix && !geo.stale) map.current?.follow(geo.fix.lngLat, navigating ? geo.fix.heading : null);
  }, [geo.fix, geo.stale, follow, navigating]);

  useEffect(() => {
    if (!guiding || !prog || !route) return;
    offsets.current = [...offsets.current.slice(-5), prog.offRouteM];
    if (isOffRoute(offsets.current) && Date.now() - lastReroute.current > 30_000 && canGuide) {
      lastReroute.current = Date.now();
      flash("Off route — recalculating");
      void requestRoute(route.dest, { reroute: true });
    }
    // voice (Web Speech API, opt-in; may be unsupported in some car browsers)
    if (!muted && prog.nextStepIndex != null && prog.distanceToNextM != null && "speechSynthesis" in window) {
      const step = route.route.steps[prog.nextStepIndex];
      const bucket = prog.distanceToNextM < 60 ? "now" : prog.distanceToNextM < 350 ? "soon" : "";
      const key = `${prog.nextStepIndex}:${bucket}`;
      if (bucket && key !== lastSpoken.current && step?.instruction) {
        lastSpoken.current = key;
        const u = new SpeechSynthesisUtterance(
          bucket === "now" ? step.instruction : `In ${formatDistance(prog.distanceToNextM, units.distance)}, ${step.instruction}`,
        );
        u.lang = locale === "ka" ? "ka-GE" : "en-US";
        window.speechSynthesis.speak(u);
      }
    }
  }, [prog, guiding, route, canGuide, requestRoute, flash, muted, units.distance, locale]);

  /* ── chargers ── */
  const loadChargers = useCallback(async () => {
    const c = map.current?.getCenter() ?? props.initialCenter;
    try {
      const f = chargerFilter === "all" ? "" : `&current=${chargerFilter}`;
      const d = await api<{ chargers: Charger[]; meta: ProviderMeta }>(`/api/chargers?lat=${c[1]}&lon=${c[0]}&radius=10${f}`);
      setChargers(d.chargers);
      setChargerMeta(d.meta);
      map.current?.setChargers(d.chargers);
    } catch (e) {
      setError((e as Error).message);
    }
  }, [chargerFilter, props.initialCenter]);

  useEffect(() => {
    if (chargersOn) void loadChargers();
    else map.current?.setChargers([]);
  }, [chargersOn, loadChargers]);

  /* ── weather ── */
  useEffect(() => {
    if (!props.flags.weather) return;
    const load = () => {
      const c = geo.fix?.lngLat ?? map.current?.getCenter() ?? props.initialCenter;
      api<{ weather: Weather; meta: ProviderMeta }>(`/api/weather?lat=${c[1].toFixed(2)}&lon=${c[0].toFixed(2)}`)
        .then(setWeather)
        .catch(() => setWeather(null));
    };
    const t0 = setTimeout(load, 1500);
    const t1 = setInterval(load, 10 * 60_000);
    return () => {
      clearTimeout(t0);
      clearInterval(t1);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [props.flags.weather]);

  /* ── destination handed in via URL / inbox ── */
  useEffect(() => {
    const d = props.initialDestination;
    if (!d) return;
    const p: Place = { id: `in_${d.lat}_${d.lon}`, name: d.name, address: d.address, lat: d.lat, lon: d.lon, category: null, source: "inbox" };
    const timer = setTimeout(() => choosePlace(p), 600);
    return () => clearTimeout(timer);
  }, [props.initialDestination, choosePlace]);

  const openInboxItem = useCallback(
    (it: InboxItemDTO) => {
      void inbox.act(it.id, "navigate");
      setInboxOpen(false);
      inbox.clearFresh();
      const p: Place = { id: it.id, name: it.name, address: it.address ?? "", lat: it.lat, lon: it.lon, category: null, source: "inbox" };
      choosePlace(p);
    },
    [inbox, choosePlace],
  );

  /* ── send to car / save ── */
  const sendToCar = useCallback(
    async (p: Place, deviceId: string) => {
      setSendState("sending");
      try {
        await api("/api/inbox", { method: "POST", body: JSON.stringify({ deviceId, name: p.name, address: p.address, lat: p.lat, lon: p.lon }) });
        setSendState("sent");
        flash("Sent to your car browser");
      } catch (e) {
        setSendState(null);
        setError((e as Error).message);
      }
    },
    [flash],
  );
  const savePlace = useCallback(
    async (p: Place) => {
      try {
        await api("/api/places", { method: "POST", body: JSON.stringify({ kind: "favorite", label: p.name.slice(0, 60), name: p.name, address: p.address, lat: p.lat, lon: p.lon, source: p.source }) });
        flash("Saved to your places");
      } catch (e) {
        setError((e as Error).message);
      }
    },
    [flash],
  );

  const onMapClick = useCallback(
    async () => {
      if (navigating) return;
      setLayersOpen(false);
      if (!selected && !searchOpen) return; // single taps only dismiss panels
      setSelected(null);
      setSearchOpen(false);
    },
    [navigating, selected, searchOpen],
  );
  const onLongPress = useCallback(
    async (ll: LngLat) => {
      if (navigating) return;
      try {
        const d = await api<{ place: Place }>(`/api/geo/reverse?lat=${ll[1]}&lon=${ll[0]}&lang=${locale}`);
        choosePlace({ ...d.place, lat: ll[1], lon: ll[0] });
      } catch {
        choosePlace({ id: `pin_${ll.join("_")}`, name: "Dropped pin", address: `${ll[1].toFixed(5)}, ${ll[0].toFixed(5)}`, lat: ll[1], lon: ll[0], category: null, source: "pin" });
      }
    },
    [navigating, locale, choosePlace],
  );

  const nextStep = prog?.nextStepIndex != null && route ? route.route.steps[prog.nextStepIndex] : route?.route.steps[0];
  // Live progress only with a fresh fix; otherwise show the route totals.
  const live = navigating && canGuide && prog;
  const remainingS = live ? prog.remainingS : (route?.route.durationS ?? 0);
  const remainingM = live ? prog.remainingM : (route?.route.distanceM ?? 0);
  const eta = new Date(now + remainingS * 1000);
  const demoNotice = config.notice;

  const panelOpen = !!selected || (!!route && !navigating) || (searchOpen && q.trim().length >= 2);

  return (
    <div className={`relative h-full w-full overflow-hidden ${drive ? "" : "sm:rounded-[28px] sm:border sm:border-line sm:shadow-[var(--shadow)]"}`}>
      {!mapBroken && (
        <MapView
          ref={map}
          config={config}
          theme={theme}
          initialCenter={props.initialCenter}
          initial3D={is3D}
          lowPerf={lowPerf}
          className="absolute inset-0"
          ariaLabel="Navigation map"
          onClick={onMapClick}
          onLongPress={onLongPress}
          onUserPan={() => setFollow(false)}
          onChargerClick={(id) => {
            const c = chargers.find((x) => x.id === id);
            if (c) setSelected({ kind: "charger", charger: c });
          }}
          onUnavailable={() => setMapBroken(true)}
          onProviderError={setTilesDown}
        />
      )}
      {mapBroken && (
        <div role="status" className="absolute inset-0 grid place-items-center bg-[radial-gradient(60%_60%_at_50%_45%,#10264a,#060f22)] p-6 text-center">
          <div className="glass-strong max-w-sm p-6">
            <p className="font-semibold">The map can&apos;t be displayed in this browser</p>
            <p className="mt-2 text-sm text-muted">Search, route preview and turn-by-turn steps still work. Try 2D mode or reduce effects in the layers menu.</p>
          </div>
        </div>
      )}
      {tilesDown && !mapBroken && (
        <p role="status" className="chip absolute left-1/2 top-[5.5rem] z-30 -translate-x-1/2 !border-warn/50 bg-panel-strong text-warn sm:top-4">
          Map provider unavailable. Retrying…
        </p>
      )}

      {/* top-left: back (drive) + search OR turn card */}
      <div className="absolute left-4 top-4 z-20 flex w-[min(460px,calc(100%-2rem))] flex-col gap-3 sm:w-[min(460px,calc(100%-16rem))]">
        {arrived ? (
          <section role="status" className="flex items-center gap-3 rounded-3xl bg-gradient-to-b from-[#22c55e] to-[#15803d] p-4 text-white shadow-lg">
            <MapPin className="h-10 w-10" aria-hidden />
            <div className="flex-1">
              <div className="text-2xl font-bold">You have arrived</div>
              <div className="text-white/85">{route?.dest.name}</div>
            </div>
            <button onClick={endRoute} className="btn bg-white/20 !min-h-12">Done</button>
          </section>
        ) : waitingForGps ? (
          <section role="status" className="glass-strong !rounded-3xl p-4">
            <div className="flex items-center gap-3">
              <Loader2 className="h-8 w-8 animate-spin text-accent-2" aria-hidden />
              <div className="flex-1">
                <div className="text-xl font-bold">Waiting for GPS…</div>
                <div className="text-sm text-muted">
                  {geo.status === "denied" ? t["gps.denied"] : "Guidance starts when your browser reports a current position."}
                </div>
              </div>
              <button onClick={() => setNavigating(false)} className="btn btn-ghost !min-h-11">{t["common.cancel"]}</button>
            </div>
          </section>
        ) : guiding && nextStep ? (
          <section aria-live="polite" className="rounded-[26px] border border-white/25 bg-gradient-to-b from-[#3d93ff] to-[#1260e6] p-4 text-white shadow-[0_18px_40px_-12px_rgba(30,110,255,0.9),inset_0_1px_0_rgba(255,255,255,0.35)] sm:p-5">
            <div className="flex items-center gap-4">
              <ManeuverIcon maneuver={nextStep.maneuver} className="h-12 w-12 shrink-0" strokeWidth={2.75} />
              <div className="min-w-0">
                <div className="text-4xl font-semibold leading-none tracking-tight">
                  {prog?.distanceToNextM != null ? formatDistance(prog.distanceToNextM, units.distance) : "—"}
                </div>
                <div className="mt-1.5 text-lg font-medium leading-snug">{nextStep.instruction || "Continue"}</div>
              </div>
            </div>
            {geo.stale && <p className="mt-2 rounded-xl bg-black/25 px-3 py-1.5 text-sm">{t["gps.stale"]}</p>}
          </section>
        ) : (
          <div className="relative">
            <div className="glass-strong flex items-center gap-2 !rounded-[20px] px-2 transition-shadow focus-within:shadow-[0_0_0_2px_var(--accent-2),var(--shadow-sm)]">
              {drive && (
                <Link href="/app" aria-label="Back to dashboard" className="grid h-12 w-12 place-items-center rounded-xl text-muted hover:text-text">
                  <ArrowLeft className="h-6 w-6" />
                </Link>
              )}
              <Search className={`h-6 w-6 shrink-0 text-muted ${drive ? "" : "ml-2"}`} aria-hidden />
              <input
                ref={searchInput}
                value={q}
                onChange={(e) => {
                  setQ(e.target.value);
                  setSearchOpen(true);
                }}
                onFocus={() => setSearchOpen(true)}
                placeholder={t["search.placeholder"]}
                aria-label={t["search.placeholder"]}
                className="h-14 min-w-0 flex-1 bg-transparent text-[17px] text-text outline-none placeholder:text-faint focus-visible:outline-none"
                enterKeyHint="search"
                autoComplete="off"
              />
              {searching && <Loader2 className="h-5 w-5 animate-spin text-muted" aria-hidden />}
              {q && (
                <button aria-label="Clear search" onClick={() => setQ("")} className="grid h-12 w-12 place-items-center text-muted">
                  <X className="h-5 w-5" />
                </button>
              )}
            </div>
            {searchOpen && (q.trim().length >= 2 || recentSearches.length > 0) && (
              <div className="glass-strong rise mt-2 max-h-[55vh] overflow-y-auto !rounded-[20px] p-2" role="listbox" aria-label="Search results">
                {q.trim().length < 2 && <p className="px-3 pb-1 pt-2 text-xs font-semibold uppercase tracking-wide text-faint">{t["search.recent"]}</p>}
                {(q.trim().length >= 2 ? results : recentSearches).map((p) => (
                  <button
                    key={p.id}
                    role="option"
                    aria-selected={false}
                    onClick={() => choosePlace(p)}
                    className="flex w-full items-center gap-3 rounded-2xl px-3 py-3 text-left transition-colors hover:bg-white/[0.06]"
                  >
                    <span className="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-accent/15 text-accent-2">
                      <MapPin className="h-5 w-5" aria-hidden />
                    </span>
                    <span className="min-w-0">
                      <span className="block truncate text-[17px] font-semibold">{p.name}</span>
                      <span className="block truncate text-sm text-muted">{p.address}</span>
                    </span>
                  </button>
                ))}
                {q.trim().length >= 2 && searching && results.length === 0 && (
                  <p className="flex items-center gap-2 px-3 py-3 text-muted"><Loader2 className="h-4 w-4 animate-spin" aria-hidden /> Searching…</p>
                )}
                {q.trim().length >= 2 && !searching && results.length === 0 && <p className="px-3 py-3 text-muted">{t["search.none"]}</p>}
                {searchMeta && q.trim().length >= 2 && (
                  <p className="px-3 pb-1 pt-2 text-[11px] text-faint">
                    {searchMeta.attribution}
                    {searchMeta.mode === "demo" && ` · ${t["common.demo"]}`}
                  </p>
                )}
              </div>
            )}
          </div>
        )}

        {/* route preview card */}
        {route && !navigating && (
          <section className="glass-strong rise !rounded-[24px] p-4 sm:p-5" aria-label={t["route.preview"]}>
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <p className="eyebrow">{t["route.preview"]}</p>
                <h2 className="mt-1 truncate text-xl font-semibold tracking-tight">{route.dest.name}</h2>
                {route.dest.address && <p className="truncate text-sm text-muted">{route.dest.address}</p>}
              </div>
              <button onClick={endRoute} aria-label={t["route.end"]} className="ctl !h-10 !w-10 shrink-0 !rounded-xl">
                <X className="h-5 w-5" />
              </button>
            </div>
            <dl className="mt-4 grid grid-cols-3 gap-2 text-center">
              <div className="rounded-2xl bg-white/[0.05] px-2 py-2.5">
                <dt className="text-[11px] uppercase tracking-wide text-faint">Time</dt>
                <dd className="text-lg font-semibold">{formatDuration(route.route.durationS)}</dd>
              </div>
              <div className="rounded-2xl bg-white/[0.05] px-2 py-2.5">
                <dt className="text-[11px] uppercase tracking-wide text-faint">Distance</dt>
                <dd className="text-lg font-semibold">{formatDistance(route.route.distanceM, units.distance)}</dd>
              </div>
              <div className="rounded-2xl bg-white/[0.05] px-2 py-2.5">
                <dt className="text-[11px] uppercase tracking-wide text-faint">{t["route.from"]}</dt>
                <dd className="truncate pt-1 text-sm font-semibold">{route.originLabel}</dd>
              </div>
            </dl>
            {route.meta.notice && (
              <p className={`mt-3 rounded-xl px-3 py-2 text-sm ${route.meta.mode === "demo" ? "border border-warn/50 bg-warn/10 text-warn" : "bg-white/5 text-muted"}`}>
                {route.meta.notice}
              </p>
            )}
            {!canGuide && <p className="mt-3 rounded-xl bg-white/5 px-3 py-2 text-sm text-muted">{t["route.noGps"]}</p>}
            <div className="mt-3 flex gap-2">
              <button onClick={startGuidance} className="btn btn-primary flex-1 !min-h-[3.25rem] text-[17px]">
                <Navigation2 className="h-5 w-5" aria-hidden /> {t["route.start"]}
              </button>
            </div>
            <details className="mt-3">
              <summary className="cursor-pointer text-sm font-semibold text-accent-2">Turn-by-turn steps ({route.route.steps.length})</summary>
              <ol className="mt-2 max-h-56 space-y-1 overflow-y-auto pr-1 text-[15px]">
                {route.route.steps.map((s, i) => {
                  return (
                    <li key={i} className="flex items-center gap-3 rounded-lg px-2 py-1.5">
                      <ManeuverIcon maneuver={s.maneuver} className="h-5 w-5 shrink-0 text-accent-2" />
                      <span className="flex-1">{s.instruction}</span>
                      <span className="text-sm text-muted">{s.distanceM ? formatDistance(s.distanceM, units.distance) : ""}</span>
                    </li>
                  );
                })}
              </ol>
              <p className="mt-2 text-[11px] text-faint">{route.meta.attribution} · No live traffic.</p>
            </details>
          </section>
        )}

        {error && (
          <p role="alert" className="flex items-start gap-2 rounded-[18px] border border-bad/50 bg-[#2a0f14]/92 px-4 py-3 text-sm text-[#ffd0d0] shadow-[var(--shadow-sm)]">
            <span className="flex-1">{error}</span>
            <button aria-label="Dismiss error" onClick={() => setError(null)}>
              <X className="h-4 w-4" />
            </button>
          </p>
        )}
      </div>

      {/* top-right chips */}
      <div className={`absolute right-4 top-[5.25rem] z-20 items-center gap-2 sm:top-4 sm:flex ${panelOpen || navigating ? "hidden" : "flex"}`}>
        {!inbox.online && (
          <span className="glass-strong inline-flex items-center gap-1.5 whitespace-nowrap !rounded-full px-3 py-1.5 text-sm font-semibold sm:px-3.5 sm:py-2 sm:text-[15px] !border-warn/50 text-warn">
            <WifiOff className="h-4 w-4" aria-hidden /> Offline
          </span>
        )}
        {weather && !navigating && (
          <span className="glass-strong inline-flex items-center gap-1.5 whitespace-nowrap !rounded-full px-3 py-1.5 text-sm font-semibold sm:px-3.5 sm:py-2 sm:text-[15px]" title={`${weather.meta.attribution} · observed ${relativeTime(weather.weather.observedAt)}`}>
            <CloudSun className="h-5 w-5 text-[#ffd36b]" aria-hidden />
            {formatTemp(weather.weather.tempC, units.temperature)}
            {weather.meta.mode === "demo" && <span className="text-[10px] font-bold uppercase text-warn">demo</span>}
            {weather.meta.stale && <span className="text-[10px] text-warn">stale</span>}
          </span>
        )}
        {inbox.paired && (
          <button onClick={() => setInboxOpen((v) => !v)} className="glass-strong inline-flex items-center gap-1.5 whitespace-nowrap !rounded-full px-3 py-1.5 text-sm font-semibold sm:px-3.5 sm:py-2 sm:text-[15px] relative" aria-label={t["inbox.title"]}>
            <Inbox className="h-5 w-5" aria-hidden />
            {inbox.items.filter((i) => i.state === "received" || i.state === "sent").length > 0 && (
              <span className="grid h-6 min-w-6 place-items-center rounded-full bg-accent px-1.5 text-xs font-bold text-white">
                {inbox.items.filter((i) => i.state === "received" || i.state === "sent").length}
              </span>
            )}
          </button>
        )}
      </div>

      {/* right toolbar (single, compact) */}
      <div
        className={`glass-strong absolute right-3 top-1/2 z-20 -translate-y-1/2 flex-col gap-1.5 !rounded-[22px] p-1.5 sm:right-4 ${panelOpen ? "hidden sm:flex" : "flex"}`}
        role="toolbar"
        aria-label="Map controls"
      >
        <button
          className="ctl !border-transparent !bg-transparent !shadow-none hover:!bg-white/[0.08]"
          aria-label={is3D ? t["map.2d"] : t["map.3d"]}
          title={is3D ? "Switch to 2D" : "Switch to 3D"}
          onClick={() => {
            const v = !is3D;
            setPref3D(v ? "on" : "off");
            map.current?.set3D(v);
          }}
        >
          {is3D ? <Square className="h-6 w-6" aria-hidden /> : <Box className="h-6 w-6" aria-hidden />}
          <span className="sr-only">{is3D ? "2D" : "3D"}</span>
        </button>
        <button
          className={`ctl !border-transparent !shadow-none ${follow ? "" : "!bg-transparent hover:!bg-white/[0.08]"}`}
          aria-label={t["map.recenter"]}
          aria-pressed={follow}
          onClick={() => {
            if (geo.status !== "watching") geo.start();
            setFollow(true);
            if (geo.fix) map.current?.follow(geo.fix.lngLat, null);
          }}
        >
          <LocateFixed className="h-6 w-6" aria-hidden />
        </button>
        <button
          className={`ctl !border-transparent !shadow-none ${layersOpen ? "!bg-white/[0.12]" : "!bg-transparent hover:!bg-white/[0.08]"}`}
          aria-label={t["map.layers"]}
          aria-expanded={layersOpen}
          onClick={() => setLayersOpen((v) => !v)}
        >
          <Layers3 className="h-6 w-6" aria-hidden />
        </button>
        <button
          className="ctl !border-transparent !bg-transparent !shadow-none hover:!bg-white/[0.08]"
          aria-label={muted ? t["map.unmute"] : t["map.mute"]}
          onClick={() => {
            const m = !muted;
            setVoicePref(m ? "off" : "on");
            if (m && "speechSynthesis" in window) window.speechSynthesis.cancel();
          }}
        >
          {muted ? <VolumeX className="h-6 w-6" aria-hidden /> : <Volume2 className="h-6 w-6" aria-hidden />}
        </button>
      </div>

      {layersOpen && (
        <div className="glass-strong rise absolute right-[5rem] top-1/2 z-30 w-[min(17rem,calc(100%-6rem))] -translate-y-1/2 !rounded-[22px] p-3 sm:right-[5.5rem]">
          <p className="eyebrow px-2 pb-2">{t["map.layers"]}</p>
          <div className="grid grid-cols-2 gap-2">
            {(["dark", "light"] as const).map((th) => (
              <button
                key={th}
                onClick={() => setDomTheme(th)}
                aria-pressed={theme === th}
                className={`rounded-xl border px-3 py-3 text-sm font-semibold capitalize transition-colors ${theme === th ? "border-accent bg-accent/20" : "border-line hover:bg-white/[0.05]"}`}
              >
                {th}
              </button>
            ))}
          </div>
          <label className="mt-3 flex items-center justify-between gap-3 rounded-xl px-2 py-2 text-[15px]">
            <span>
              Reduce effects
              <span className="block text-xs text-faint">For slower car computers</span>
            </span>
            <input
              type="checkbox"
              className="h-6 w-6 shrink-0 accent-[var(--accent)]"
              checked={lowPerf}
              onChange={(e) => setLowPerfMode(e.target.checked)}
            />
          </label>
          {props.flags.chargers && (
            <>
              <label className="mt-1 flex items-center justify-between rounded-xl px-2 py-2 text-[15px]">
                <span className="flex items-center gap-2">
                  <Zap className="h-5 w-5 text-[#9b8cff]" aria-hidden /> Chargers
                </span>
                <input type="checkbox" className="h-6 w-6 accent-[var(--accent)]" checked={chargersOn} onChange={(e) => setChargersOn(e.target.checked)} />
              </label>
              {chargersOn && (
                <div className="mt-1 grid grid-cols-3 gap-1" role="radiogroup" aria-label="Charger type">
                  {(["all", "AC", "DC"] as const).map((f) => (
                    <button
                      key={f}
                      role="radio"
                      aria-checked={chargerFilter === f}
                      onClick={() => setChargerFilter(f)}
                      className={`rounded-lg px-2 py-2 text-sm font-semibold ${chargerFilter === f ? "bg-accent text-white" : "bg-white/5"}`}
                    >
                      {f === "all" ? "All" : f}
                    </button>
                  ))}
                </div>
              )}
              {chargersOn && chargerMeta && (
                <p className="mt-2 px-1 text-[11px] leading-snug text-faint">
                  {chargers.length} found · {chargerMeta.attribution}
                  {chargerMeta.notice ? ` · ${chargerMeta.notice}` : ""}
                </p>
              )}
            </>
          )}
        </div>
      )}

      {/* bottom: place sheet / charger sheet / navigation bar */}
      <div className="absolute inset-x-4 bottom-4 z-20 flex justify-center">
        {guiding && route ? (
          <section className="glass-strong rise flex w-full max-w-[820px] items-center gap-3 !rounded-[26px] px-4 py-3 sm:gap-4 sm:px-5" aria-label="Trip progress">
            <span className="hidden h-12 w-12 shrink-0 place-items-center rounded-2xl bg-accent/15 text-accent-2 sm:grid">
              <Car className="h-6 w-6" aria-hidden />
            </span>
            <div className="min-w-0 flex-1">
              <div className="text-2xl font-semibold tracking-tight">{formatDuration(remainingS)}</div>
              <div className="truncate text-sm text-muted sm:text-base">
                {formatDistance(remainingM, units.distance)} · arrive {formatClock(eta, units.time, locale)}
              </div>
            </div>
            <button onClick={() => setNavigating(false)} className="btn btn-ghost !min-h-12 !px-4">
              Pause
            </button>
            <button onClick={endRoute} className="btn btn-danger !min-h-12 !px-4 sm:!px-6">
              {t["route.end"]}
            </button>
          </section>
        ) : selected?.kind === "place" ? (
          <section className="glass-strong rise w-full max-w-[820px] !rounded-[24px] p-4 sm:p-5" aria-label="Selected place">
            <div className="flex items-start gap-3">
              <span className="grid h-11 w-11 shrink-0 place-items-center rounded-2xl bg-gradient-to-b from-[#ff6b6b] to-[#e5484d] text-white shadow-[0_8px_20px_-8px_rgba(229,72,77,0.8)]">
                <MapPin className="h-5 w-5" aria-hidden />
              </span>
              <div className="min-w-0 flex-1">
                <h2 className="truncate text-xl font-semibold tracking-tight">{selected.place.name}</h2>
                <p className="truncate text-muted">{selected.place.address}</p>
              </div>
              <button aria-label={t["common.close"]} onClick={() => { setSelected(null); map.current?.setDestination(null); }} className="ctl !h-10 !w-10 shrink-0 !rounded-xl">
                <X className="h-5 w-5" />
              </button>
            </div>
            <div className="mt-4 flex flex-wrap gap-2">
              <button onClick={() => requestRoute(selected.place)} disabled={routing} className="btn btn-primary !min-h-[3.25rem] flex-1 text-[17px]">
                {routing ? <Loader2 className="h-5 w-5 animate-spin" aria-hidden /> : <Navigation2 className="h-5 w-5" aria-hidden />} {t["route.directions"]}
              </button>
              <button onClick={() => savePlace(selected.place)} className="btn btn-ghost !min-h-[3.25rem]">
                <Star className="h-5 w-5" aria-hidden /> {t["place.save"]}
              </button>
              {props.flags.send_to_car && props.devices.filter((d) => d.id !== inbox.deviceId).length > 0 && (
                <SendMenu
                  devices={props.devices.filter((d) => d.id !== inbox.deviceId)}
                  label={t["place.sendToCar"]}
                  busy={sendState === "sending"}
                  onSend={(id) => sendToCar(selected.place, id)}
                />
              )}
            </div>
          </section>
        ) : selected?.kind === "charger" ? (
          <ChargerSheet
            c={selected.charger}
            meta={chargerMeta}
            units={units}
            onClose={() => setSelected(null)}
            onRoute={() =>
              requestRoute({ id: selected.charger.id, name: selected.charger.title, address: selected.charger.address, lat: selected.charger.lat, lon: selected.charger.lon, category: "charger", source: "chargers" })
            }
          />
        ) : null}
      </div>

      {/* inbox panel */}
      {inboxOpen && (
        <section className="glass-strong absolute right-4 top-20 z-30 w-[min(380px,calc(100%-2rem))] !rounded-2xl p-3" aria-label={t["inbox.title"]}>
          <div className="flex items-center justify-between px-2 pb-2">
            <h2 className="font-semibold">{t["inbox.title"]}</h2>
            <button aria-label={t["common.close"]} onClick={() => setInboxOpen(false)}>
              <X className="h-5 w-5" />
            </button>
          </div>
          {inbox.items.length === 0 && <p className="px-2 pb-2 text-sm text-muted">{t["inbox.empty"]}</p>}
          <ul className="grid gap-2">
            {inbox.items.map((it) => (
              <li key={it.id} className="rounded-xl border border-line p-3">
                <div className="flex items-start gap-2">
                  <MapPin className="mt-0.5 h-5 w-5 text-accent-2" aria-hidden />
                  <div className="min-w-0 flex-1">
                    <p className="truncate font-semibold">{it.name}</p>
                    <p className="text-xs text-muted">
                      {it.sender_label ?? "Sent"} · {relativeTime(it.sent_at)}
                    </p>
                  </div>
                </div>
                <div className="mt-2 grid grid-cols-2 gap-2">
                  <button className="btn btn-primary !min-h-11 text-sm" onClick={() => openInboxItem(it)}>
                    {t["inbox.navigate"]}
                  </button>
                  <button className="btn btn-ghost !min-h-11 text-sm" onClick={() => inbox.act(it.id, "dismiss")}>
                    {t["inbox.dismiss"]}
                  </button>
                </div>
              </li>
            ))}
          </ul>
        </section>
      )}

      {/* new destination arrived */}
      {inbox.fresh && !inboxOpen && (
        <div role="status" className="glass-strong absolute left-1/2 top-24 z-40 flex w-[min(520px,calc(100%-2rem))] -translate-x-1/2 items-center gap-3 !rounded-2xl p-3">
          <Send className="h-6 w-6 text-accent-2" aria-hidden />
          <div className="min-w-0 flex-1">
            <p className="text-sm text-muted">New destination from your phone</p>
            <p className="truncate font-semibold">{inbox.fresh.name}</p>
          </div>
          <button className="btn btn-primary !min-h-11" onClick={() => openInboxItem(inbox.fresh!)}>
            {t["inbox.navigate"]} <ChevronRight className="h-4 w-4" aria-hidden />
          </button>
          <button aria-label={t["common.close"]} onClick={inbox.clearFresh}>
            <X className="h-5 w-5" />
          </button>
        </div>
      )}

      {/* status line: GPS & demo notices (never hide the map) */}
      <div className="pointer-events-none absolute bottom-4 left-4 z-10 hidden max-w-[40%] flex-col gap-1 lg:flex">
        {geo.status === "denied" && <p className="chip bg-panel-strong text-xs text-warn">{t["gps.denied"]}</p>}
        {(geo.status === "unavailable" || geo.status === "unsupported") && <p className="chip bg-panel-strong text-xs text-warn">{t["gps.unavailable"]}</p>}
        {demoNotice && !navigating && !selected && <p className="chip bg-panel-strong text-xs text-muted">{demoNotice}</p>}
      </div>
      {(geo.status === "idle" || geo.status === "prompt") && !navigating && !selected && !route && (
        <button onClick={geo.start} className="btn btn-ghost glass-strong absolute bottom-4 right-4 z-20 !min-h-12">
          <LocateFixed className="h-5 w-5" aria-hidden /> Use my location
        </button>
      )}

      {toast && (
        <div role="status" className="glass-strong rise absolute bottom-28 left-1/2 z-40 -translate-x-1/2 !rounded-full px-5 py-3 text-[15px] font-medium">
          {toast}
        </div>
      )}
    </div>
  );
}

function SendMenu({ devices, label, onSend, busy }: { devices: { id: string; name: string }[]; label: string; onSend: (id: string) => void; busy: boolean }) {
  const [open, setOpen] = useState(false);
  if (devices.length === 1) {
    return (
      <button onClick={() => onSend(devices[0].id)} disabled={busy} className="btn btn-ghost !min-h-14">
        <Send className="h-5 w-5" aria-hidden /> {label}
      </button>
    );
  }
  return (
    <div className="relative">
      <button onClick={() => setOpen((v) => !v)} className="btn btn-ghost !min-h-14" aria-expanded={open}>
        <Send className="h-5 w-5" aria-hidden /> {label}
      </button>
      {open && (
        <div className="glass-strong absolute bottom-16 right-0 w-56 !rounded-xl p-1">
          {devices.map((d) => (
            <button key={d.id} onClick={() => { setOpen(false); onSend(d.id); }} className="block w-full rounded-lg px-3 py-3 text-left hover:bg-white/5">
              {d.name}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function ChargerSheet({ c, meta, units, onClose, onRoute }: { c: Charger; meta: ProviderMeta | null; units: Units; onClose: () => void; onRoute: () => void }) {
  return (
    <section className="glass-strong w-full max-w-[820px] !rounded-3xl p-4" aria-label="Charger details">
      <div className="flex items-start gap-3">
        <Zap className="mt-1 h-7 w-7 shrink-0 text-[#9b8cff]" aria-hidden />
        <div className="min-w-0 flex-1">
          <h2 className="truncate text-xl font-bold">{c.title}</h2>
          <p className="truncate text-muted">
            {c.address || "Address not provided"}
            {c.distanceKm != null && ` · ${formatDistance(c.distanceKm * 1000, units.distance)}`}
          </p>
        </div>
        <button aria-label="Close" onClick={onClose} className="grid h-11 w-11 place-items-center rounded-xl border border-line-strong">
          <X className="h-5 w-5" />
        </button>
      </div>
      <ul className="mt-3 flex flex-wrap gap-2">
        {c.connectors.map((k, i) => (
          <li key={i} className="chip">
            {k.type} · {k.current}
            {k.powerKw != null ? ` · ${k.powerKw} kW` : " · power unknown"}
            {k.quantity ? ` ×${k.quantity}` : ""}
          </li>
        ))}
      </ul>
      <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-1 text-sm">
        <dt className="text-muted">Live availability</dt>
        <dd className="font-semibold text-warn">Unknown</dd>
        <dt className="text-muted">Reported status</dt>
        <dd>
          {c.reportedStatus ?? "Not reported"}
          {c.statusUpdatedAt && <span className="text-muted"> ({relativeTime(c.statusUpdatedAt)})</span>}
        </dd>
        <dt className="text-muted">Operator</dt>
        <dd>{c.operator ?? "Unknown"}</dd>
        <dt className="text-muted">Source</dt>
        <dd>
          {c.dataProvider ?? meta?.source}
          {c.license && <span className="text-muted"> · {c.license}</span>}
        </dd>
      </dl>
      <button onClick={onRoute} className="btn btn-primary mt-4 w-full !min-h-14 text-lg">
        <Navigation2 className="h-5 w-5" aria-hidden /> Route to charger
      </button>
    </section>
  );
}
