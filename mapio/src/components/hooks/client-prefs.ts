"use client";

import { useCallback, useEffect, useState, useSyncExternalStore } from "react";

/* External-store hooks: SSR-safe reads of DOM/localStorage/online state without
   setState-in-effect cascades. Server snapshots return the defaults. */

const PREF_EVENT = "mapio-pref";

function subscribeDom(cb: () => void) {
  const obs = new MutationObserver(cb);
  obs.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme", "data-perf"] });
  return () => obs.disconnect();
}

/** Current resolved theme from <html data-theme>. */
export function useDomTheme(): "dark" | "light" {
  return useSyncExternalStore(
    subscribeDom,
    () => (document.documentElement.dataset.theme === "light" ? "light" : "dark"),
    () => "dark",
  );
}

export function setDomTheme(theme: "dark" | "light") {
  document.documentElement.dataset.theme = theme;
  try {
    localStorage.setItem("mapio_theme", theme);
  } catch {}
}

export function useLowPerf(): boolean {
  return useSyncExternalStore(subscribeDom, () => document.documentElement.dataset.perf === "low", () => false);
}

function subscribePrefs(cb: () => void) {
  window.addEventListener("storage", cb);
  window.addEventListener(PREF_EVENT, cb);
  return () => {
    window.removeEventListener("storage", cb);
    window.removeEventListener(PREF_EVENT, cb);
  };
}

function readLocal(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

/** A string preference persisted in localStorage (per-browser convenience only). */
export function useLocalPref(key: string, fallback: string): [string, (v: string) => void] {
  const value = useSyncExternalStore(subscribePrefs, () => readLocal(key) ?? fallback, () => fallback);
  const set = useCallback(
    (v: string) => {
      try {
        localStorage.setItem(key, v);
      } catch {}
      window.dispatchEvent(new Event(PREF_EVENT));
    },
    [key],
  );
  return [value, set];
}

/** JSON array stored in localStorage; snapshot is cached by raw string for stable identity. */
const jsonCache = new Map<string, { raw: string | null; value: unknown }>();
export function useLocalJson<T>(key: string, fallback: T): [T, (v: T) => void] {
  const getSnap = () => {
    const raw = readLocal(key);
    const hit = jsonCache.get(key);
    if (hit && hit.raw === raw) return hit.value as T;
    let value: T = fallback;
    try {
      value = raw ? (JSON.parse(raw) as T) : fallback;
    } catch {}
    jsonCache.set(key, { raw, value });
    return value;
  };
  const value = useSyncExternalStore(subscribePrefs, getSnap, () => fallback);
  const set = useCallback(
    (v: T) => {
      try {
        localStorage.setItem(key, JSON.stringify(v));
      } catch {}
      window.dispatchEvent(new Event(PREF_EVENT));
    },
    [key],
  );
  return [value, set];
}

function subscribeOnline(cb: () => void) {
  window.addEventListener("online", cb);
  window.addEventListener("offline", cb);
  return () => {
    window.removeEventListener("online", cb);
    window.removeEventListener("offline", cb);
  };
}
export function useOnline() {
  return useSyncExternalStore(subscribeOnline, () => navigator.onLine, () => true);
}

/** A clock that ticks every `ms` (for ETAs and "x min ago" labels). */
export function useNow(ms = 15_000) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), ms);
    return () => clearInterval(t);
  }, [ms]);
  return now;
}
