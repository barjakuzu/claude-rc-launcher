/** Locale-aware formatting helpers (shared client/server). */
export type Units = { distance: "km" | "mi"; temperature: "c" | "f"; time: "24h" | "12h" };
export const DEFAULT_UNITS: Units = { distance: "km", temperature: "c", time: "24h" };

export function formatDistance(m: number, unit: "km" | "mi") {
  if (unit === "mi") {
    const mi = m / 1609.344;
    return mi < 0.1 ? `${Math.round(m * 3.28084 / 10) * 10} ft` : `${mi < 10 ? mi.toFixed(1) : Math.round(mi)} mi`;
  }
  return m < 1000 ? `${Math.round(m / 10) * 10} m` : `${m < 10000 ? (m / 1000).toFixed(1) : Math.round(m / 1000)} km`;
}

export function formatDuration(s: number) {
  const h = Math.floor(s / 3600);
  const m = Math.max(1, Math.round((s % 3600) / 60));
  return h ? `${h} h ${m} min` : `${m} min`;
}

export function formatTemp(c: number, unit: "c" | "f") {
  return unit === "f" ? `${Math.round((c * 9) / 5 + 32)}°F` : `${Math.round(c)}°C`;
}

export function formatClock(d: Date, time: "24h" | "12h", locale = "en") {
  return d.toLocaleTimeString(locale === "ka" ? "ka-GE" : "en-GB", {
    hour: "numeric",
    minute: "2-digit",
    hour12: time === "12h",
  });
}

export function formatMoney(minor: number, currency: string, locale = "en") {
  return new Intl.NumberFormat(locale === "ka" ? "ka-GE" : "en-US", {
    style: "currency",
    currency,
    minimumFractionDigits: minor % 100 === 0 ? 0 : 2,
  }).format(minor / 100);
}

export function formatDate(d: string | Date | null | undefined, locale = "en") {
  if (!d) return "—";
  return new Date(d).toLocaleDateString(locale === "ka" ? "ka-GE" : "en-US", {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

export function relativeTime(d: string | Date, now = Date.now()) {
  const s = Math.round((now - new Date(d).getTime()) / 1000);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} d ago`;
}

/** WMO weather code → short label + emoji-free icon key. */
export function weatherLabel(code: number): { label: string; icon: "sun" | "cloud-sun" | "cloud" | "fog" | "rain" | "snow" | "storm" } {
  if (code === 0) return { label: "Clear", icon: "sun" };
  if (code <= 2) return { label: "Partly cloudy", icon: "cloud-sun" };
  if (code === 3) return { label: "Overcast", icon: "cloud" };
  if (code <= 48) return { label: "Fog", icon: "fog" };
  if (code <= 67 || (code >= 80 && code <= 82)) return { label: "Rain", icon: "rain" };
  if (code <= 77 || code === 85 || code === 86) return { label: "Snow", icon: "snow" };
  return { label: "Thunderstorm", icon: "storm" };
}
