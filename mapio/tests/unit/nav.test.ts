import { describe, expect, it } from "vitest";
import { isOffRoute, isStale, prepareRoute, progress, smoothFix, distM } from "@/lib/nav";
import { fixtureRoute } from "@/lib/providers/fixtures";
import type { LngLat } from "@/lib/providers/types";

// Fixture route: Freedom Square → Holy Trinity Cathedral (synthetic L-shape, FIXTURE geometry)
const from: LngLat = [44.8015, 41.6934];
const to: LngLat = [44.8166, 41.6974];
const route = fixtureRoute(from, to);
const pr = prepareRoute(route);

describe("route progress (fixture geometry)", () => {
  it("total length matches the route distance", () => {
    expect(Math.abs(pr.total - route.distanceM)).toBeLessThan(1);
  });

  it("at the start: nothing travelled, next step is the turn", () => {
    const p = progress(pr, from);
    expect(p.alongM).toBeLessThan(1);
    expect(p.nextStepIndex).toBe(1);
    expect(p.distanceToNextM).toBeGreaterThan(400);
    expect(p.arrived).toBe(false);
  });

  it("past the corner: next step is arrival and remaining distance shrinks", () => {
    const mid: LngLat = [44.81, 41.6974];
    const p = progress(pr, mid);
    expect(p.nextStepIndex).toBe(2);
    expect(p.remainingM).toBeLessThan(pr.total / 2);
    expect(p.remainingS).toBeGreaterThan(0);
  });

  it("at the destination: arrived", () => {
    expect(progress(pr, to).arrived).toBe(true);
  });

  it("measures off-route distance", () => {
    const away: LngLat = [44.79, 41.69];
    expect(progress(pr, away).offRouteM).toBeGreaterThan(500);
  });
});

describe("off-route detection", () => {
  it("requires consecutive far fixes", () => {
    expect(isOffRoute([100, 10, 100])).toBe(false);
    expect(isOffRoute([100, 100])).toBe(false);
    expect(isOffRoute([5, 100, 120, 90])).toBe(true);
  });
});

describe("GPS smoothing and staleness", () => {
  it("passes the first fix through and blends noisy fixes", () => {
    const a = { lngLat: from, accuracy: 10, speed: 10, heading: 90, timestamp: 1000 };
    expect(smoothFix(null, a)).toEqual(a);
    const b = { lngLat: [44.802, 41.6934] as LngLat, accuracy: 100, speed: 10, heading: null, timestamp: 2000 };
    const s = smoothFix(a, b);
    expect(distM(s.lngLat, from)).toBeLessThan(distM(b.lngLat, from));
    expect(s.heading).not.toBeNull(); // derived from movement
  });

  it("flags stale fixes", () => {
    expect(isStale(null)).toBe(true);
    expect(isStale({ lngLat: from, accuracy: 5, speed: 0, heading: null, timestamp: 0 }, 20_000)).toBe(true);
    expect(isStale({ lngLat: from, accuracy: 5, speed: 0, heading: null, timestamp: 10_000 }, 20_000)).toBe(false);
  });
});
