/** Pure navigation helpers (client + tests). No I/O. */
import type { LngLat, Route } from "@/lib/providers/types";

const R = 6371000;
const rad = (d: number) => (d * Math.PI) / 180;

export function distM(a: LngLat, b: LngLat) {
  const dLat = rad(b[1] - a[1]);
  const dLon = rad(b[0] - a[0]);
  const s = Math.sin(dLat / 2) ** 2 + Math.cos(rad(a[1])) * Math.cos(rad(b[1])) * Math.sin(dLon / 2) ** 2;
  return 2 * R * Math.asin(Math.min(1, Math.sqrt(s)));
}

export function bearing(a: LngLat, b: LngLat) {
  const y = Math.sin(rad(b[0] - a[0])) * Math.cos(rad(b[1]));
  const x = Math.cos(rad(a[1])) * Math.sin(rad(b[1])) - Math.sin(rad(a[1])) * Math.cos(rad(b[1])) * Math.cos(rad(b[0] - a[0]));
  return ((Math.atan2(y, x) * 180) / Math.PI + 360) % 360;
}

/** Projects p onto segment ab (equirectangular approximation, fine at city scale). */
function projectOnSegment(p: LngLat, a: LngLat, b: LngLat): { t: number; point: LngLat } {
  const k = Math.cos(rad(p[1]));
  const ax = a[0] * k, ay = a[1], bx = b[0] * k, by = b[1], px = p[0] * k, py = p[1];
  const dx = bx - ax, dy = by - ay;
  const len2 = dx * dx + dy * dy;
  const t = len2 === 0 ? 0 : Math.max(0, Math.min(1, ((px - ax) * dx + (py - ay) * dy) / len2));
  return { t, point: [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t] };
}

export interface PreparedRoute {
  route: Route;
  cum: number[]; // cumulative metres at each vertex
  total: number;
  stepAt: number[]; // along-route metres of each step's maneuver
}

export function prepareRoute(route: Route): PreparedRoute {
  const g = route.geometry;
  const cum = [0];
  for (let i = 1; i < g.length; i++) cum.push(cum[i - 1] + distM(g[i - 1], g[i]));
  const total = cum[cum.length - 1] ?? 0;
  const stepAt = route.steps.map((s) => locate(g, cum, s.location).along);
  return { route, cum, total, stepAt };
}

function locate(g: LngLat[], cum: number[], p: LngLat) {
  let best = { along: 0, off: Infinity, point: g[0] ?? p };
  for (let i = 1; i < g.length; i++) {
    const { t, point } = projectOnSegment(p, g[i - 1], g[i]);
    const off = distM(p, point);
    if (off < best.off) best = { along: cum[i - 1] + (cum[i] - cum[i - 1]) * t, off, point };
  }
  if (g.length === 1) best = { along: 0, off: distM(p, g[0]), point: g[0] };
  return best;
}

export interface Progress {
  alongM: number;
  offRouteM: number;
  remainingM: number;
  remainingS: number;
  nextStepIndex: number | null;
  distanceToNextM: number | null;
  arrived: boolean;
  snapped: LngLat;
}

export function progress(pr: PreparedRoute, pos: LngLat): Progress {
  const g = pr.route.geometry;
  const loc = locate(g, pr.cum, pos);
  const remainingM = Math.max(0, pr.total - loc.along);
  const remainingS = pr.total > 0 ? (pr.route.durationS * remainingM) / pr.total : 0;
  let next: number | null = null;
  for (let i = 0; i < pr.stepAt.length; i++) {
    if (pr.stepAt[i] > loc.along + 8) {
      next = i;
      break;
    }
  }
  if (next == null && pr.route.steps.length) next = pr.route.steps.length - 1;
  const distanceToNextM = next == null ? null : Math.max(0, pr.stepAt[next] - loc.along);
  const end = g[g.length - 1];
  return {
    alongM: loc.along,
    offRouteM: loc.off,
    remainingM,
    remainingS,
    nextStepIndex: next,
    distanceToNextM,
    arrived: !!end && distM(pos, end) < 30,
    snapped: loc.point,
  };
}

/** Off-route if the last N fixes were all farther than the threshold (avoids GPS-jitter reroutes). */
export function isOffRoute(recentOffsets: number[], thresholdM = 60, n = 3) {
  return recentOffsets.length >= n && recentOffsets.slice(-n).every((d) => d > thresholdM);
}

export interface Fix {
  lngLat: LngLat;
  accuracy: number;
  speed: number | null; // m/s
  heading: number | null;
  timestamp: number;
}

/** Light smoothing: blend toward new fix by accuracy; keeps heading stable at low speed. */
export function smoothFix(prev: Fix | null, next: Fix): Fix {
  if (!prev || next.timestamp - prev.timestamp > 10_000) return next;
  const w = Math.max(0.25, Math.min(1, 20 / Math.max(5, next.accuracy)));
  const lngLat: LngLat = [prev.lngLat[0] + (next.lngLat[0] - prev.lngLat[0]) * w, prev.lngLat[1] + (next.lngLat[1] - prev.lngLat[1]) * w];
  let heading = next.heading;
  if ((heading == null || Number.isNaN(heading)) && (next.speed ?? 0) > 2 && distM(prev.lngLat, next.lngLat) > 3) {
    heading = bearing(prev.lngLat, next.lngLat);
  }
  if ((next.speed ?? 0) < 1) heading = prev.heading ?? heading;
  return { ...next, lngLat, heading };
}

export const STALE_FIX_MS = 15_000;
export function isStale(fix: Fix | null, now = Date.now()) {
  return !fix || now - fix.timestamp > STALE_FIX_MS;
}
