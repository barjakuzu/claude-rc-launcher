"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { isStale, smoothFix, type Fix } from "@/lib/nav";
import { useOnline } from "@/components/hooks/client-prefs";

export type GeoStatus = "idle" | "prompt" | "watching" | "denied" | "unavailable" | "unsupported";

/**
 * Browser geolocation with consent handling, smoothing, throttling and staleness.
 * In-car browsers may not expose GPS/heading reliably — callers must handle every status.
 */
export function useGeolocation() {
  const [status, setStatus] = useState<GeoStatus>("idle");
  const [fix, setFix] = useState<Fix | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const last = useRef<Fix | null>(null);
  const watchId = useRef<number | null>(null);
  const lastEmit = useRef(0);

  const start = useCallback(() => {
    if (typeof navigator === "undefined" || !("geolocation" in navigator)) {
      setStatus("unsupported");
      return;
    }
    if (watchId.current != null) return;
    setStatus("prompt");
    watchId.current = navigator.geolocation.watchPosition(
      (p) => {
        const raw: Fix = {
          lngLat: [p.coords.longitude, p.coords.latitude],
          accuracy: p.coords.accuracy,
          speed: p.coords.speed,
          heading: p.coords.heading,
          timestamp: p.timestamp || Date.now(),
        };
        const s = smoothFix(last.current, raw);
        last.current = s;
        setStatus("watching");
        // throttle React updates to ~1 Hz
        if (Date.now() - lastEmit.current > 900) {
          lastEmit.current = Date.now();
          setFix(s);
        }
      },
      (err) => {
        setStatus(err.code === err.PERMISSION_DENIED ? "denied" : "unavailable");
        if (err.code === err.PERMISSION_DENIED && watchId.current != null) {
          navigator.geolocation.clearWatch(watchId.current);
          watchId.current = null;
        }
      },
      { enableHighAccuracy: true, maximumAge: 3000, timeout: 20_000 },
    );
  }, []);

  // Auto-start only if permission was already granted (no surprise prompts).
  useEffect(() => {
    let cancelled = false;
    const perms = (navigator as Navigator & { permissions?: Permissions }).permissions;
    perms
      ?.query({ name: "geolocation" as PermissionName })
      .then((r) => {
        if (cancelled) return;
        if (r.state === "granted") start();
        if (r.state === "denied") setStatus("denied");
      })
      .catch(() => {});
    const t = setInterval(() => setNow(Date.now()), 5000);
    return () => {
      cancelled = true;
      clearInterval(t);
      if (watchId.current != null) navigator.geolocation.clearWatch(watchId.current);
      watchId.current = null;
    };
  }, [start]);

  return { status, fix, stale: isStale(fix, now), start };
}

export interface InboxItemDTO {
  id: string;
  name: string;
  address: string | null;
  lat: number;
  lon: number;
  note: string | null;
  sender_label: string | null;
  state: string;
  sent_at: string;
}

/**
 * Send-to-Car inbox for this browser. Delivery = Supabase Realtime (when configured)
 * + polling fallback; the server returns undismissed items, so a refresh/reconnect
 * replays anything missed.
 */
export function useInbox(enabled: boolean, pollMs = 10_000) {
  const [paired, setPaired] = useState<boolean | null>(null);
  const [deviceId, setDeviceId] = useState<string | null>(null);
  const [items, setItems] = useState<InboxItemDTO[]>([]);
  const online = useOnline();
  const seen = useRef<Set<string>>(new Set());
  const initialized = useRef(false);
  const [fresh, setFresh] = useState<InboxItemDTO | null>(null);

  const refresh = useCallback(async () => {
    try {
      const r = await fetch("/api/inbox", { cache: "no-store" });
      if (!r.ok) return;
      const d = (await r.json()) as { paired: boolean; deviceId?: string; items: InboxItemDTO[] };
      setPaired(d.paired);
      setDeviceId(d.deviceId ?? null);
      setItems(d.items);
      // First successful load only records what's already there; later loads toast new arrivals.
      for (const it of d.items) {
        if (!seen.current.has(it.id)) {
          if (initialized.current && it.state !== "navigating") setFresh(it);
          seen.current.add(it.id);
        }
      }
      initialized.current = true;
    } catch {
      /* offline: next poll retries */
    }
  }, []);

  useEffect(() => {
    if (!enabled) return;
    // refresh() only sets state after awaiting the network, so this is not a synchronous cascade.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void refresh();
    const t = setInterval(() => {
      if (document.visibilityState === "visible") void refresh();
    }, pollMs);
    const on = () => void refresh(); // reconnect → replay anything missed
    const vis = () => document.visibilityState === "visible" && void refresh();
    window.addEventListener("online", on);
    document.addEventListener("visibilitychange", vis);
    return () => {
      clearInterval(t);
      window.removeEventListener("online", on);
      document.removeEventListener("visibilitychange", vis);
    };
  }, [enabled, pollMs, refresh]);

  // Supabase Realtime push (live mode only). RLS limits rows to the signed-in owner.
  useEffect(() => {
    const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
    const key = process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY;
    if (!enabled || !deviceId || !url || !key) return;
    let cleanup: (() => void) | undefined;
    (async () => {
      const { createBrowserClient } = await import("@supabase/ssr");
      const sb = createBrowserClient(url, key);
      const ch = sb
        .channel(`inbox:${deviceId}`)
        .on(
          "postgres_changes",
          { event: "INSERT", schema: "public", table: "inbox_destinations", filter: `target_device_id=eq.${deviceId}` },
          () => void refresh(),
        )
        .subscribe();
      cleanup = () => void sb.removeChannel(ch);
    })().catch(() => {});
    return () => cleanup?.();
  }, [enabled, deviceId, refresh]);

  const act = useCallback(
    async (id: string, action: "open" | "navigate" | "dismiss") => {
      await fetch(`/api/inbox/${id}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action }) });
      if (action === "dismiss") setItems((xs) => xs.filter((x) => x.id !== id));
      void refresh();
    },
    [refresh],
  );

  return { paired, deviceId, items, online, fresh, clearFresh: () => setFresh(null), act, refresh };
}

export function useDebounced<T>(value: T, ms: number) {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}
