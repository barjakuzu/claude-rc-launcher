import type { StyleSpecification } from "maplibre-gl";

/**
 * OFFLINE DEMO STYLE. A synthetic grid city around central Tbilisi used when no tile
 * provider is reachable (CI, air-gapped dev). Streets, river and buildings are generated,
 * NOT real geography; the UI shows a "synthetic map" notice whenever this style is active.
 */
const CENTER: [number, number] = [44.8015, 41.6934];

function rng(seed: number) {
  return () => {
    seed = (seed * 1664525 + 1013904223) % 4294967296;
    return seed / 4294967296;
  };
}

function build() {
  const r = rng(42);
  const roads: GeoJSON.Feature[] = [];
  const buildings: GeoJSON.Feature[] = [];
  const step = 0.0022;
  const n = 18;
  const [cx, cy] = CENTER;
  for (let i = -n; i <= n; i++) {
    const major = i % 4 === 0;
    roads.push({
      type: "Feature",
      properties: { major },
      geometry: { type: "LineString", coordinates: [[cx + i * step, cy - n * step], [cx + i * step, cy + n * step]] },
    });
    roads.push({
      type: "Feature",
      properties: { major },
      geometry: { type: "LineString", coordinates: [[cx - n * step, cy + i * step], [cx + n * step, cy + i * step]] },
    });
  }
  for (let i = -n; i < n; i++) {
    for (let j = -n; j < n; j++) {
      const x0 = cx + i * step;
      const y0 = cy + j * step;
      // river corridor (synthetic)
      if (Math.abs(i * 0.8 - j + 2) < 1.2) continue;
      const parts = r() > 0.5 ? 2 : 1;
      for (let k = 0; k < parts; k++) {
        const pad = step * 0.14;
        const w = (step - pad * 2) / parts;
        const bx = x0 + pad + k * w;
        const by = y0 + pad;
        const h = 8 + Math.floor(r() * (Math.hypot(i, j) < 6 ? 60 : 24));
        buildings.push({
          type: "Feature",
          properties: { height: h },
          geometry: {
            type: "Polygon",
            coordinates: [[[bx, by], [bx + w * 0.92, by], [bx + w * 0.92, by + step - pad * 2], [bx, by + step - pad * 2], [bx, by]]],
          },
        });
      }
    }
  }
  const river: GeoJSON.Feature = {
    type: "Feature",
    properties: {},
    geometry: {
      type: "LineString",
      coordinates: Array.from({ length: 40 }, (_, t) => {
        const j = -n + t;
        return [cx + ((j - 2) / 0.8) * step + step / 2, cy + j * step + step / 2];
      }),
    },
  };
  return { roads, buildings, river };
}

let cache: ReturnType<typeof build> | null = null;

export function fixtureStyle(theme: "dark" | "light"): StyleSpecification {
  cache ??= build();
  const dark = theme === "dark";
  return {
    version: 8,
    name: "mapio-fixture",
    sources: {
      roads: { type: "geojson", data: { type: "FeatureCollection", features: cache.roads } },
      buildings: { type: "geojson", data: { type: "FeatureCollection", features: cache.buildings } },
      river: { type: "geojson", data: { type: "FeatureCollection", features: [cache.river] } },
    },
    layers: [
      { id: "bg", type: "background", paint: { "background-color": dark ? "#0d1626" : "#cfdcec" } },
      {
        id: "river",
        type: "line",
        source: "river",
        paint: { "line-color": dark ? "#123456" : "#9fc0e8", "line-width": ["interpolate", ["linear"], ["zoom"], 12, 6, 17, 40] },
      },
      {
        id: "roads-casing",
        type: "line",
        source: "roads",
        paint: { "line-color": dark ? "#0a1120" : "#b9cbe0", "line-width": ["interpolate", ["linear"], ["zoom"], 12, 2, 17, 18] },
      },
      {
        id: "roads",
        type: "line",
        source: "roads",
        paint: {
          "line-color": dark ? ["case", ["get", "major"], "#3a4b66", "#26344a"] : ["case", ["get", "major"], "#f4f8fd", "#e4edf7"],
          "line-width": ["interpolate", ["linear"], ["zoom"], 12, ["case", ["get", "major"], 1.6, 0.8], 17, ["case", ["get", "major"], 14, 9]],
        },
      },
      {
        id: "mapio-buildings-3d",
        type: "fill-extrusion",
        source: "buildings",
        minzoom: 13,
        paint: {
          "fill-extrusion-color": dark ? "#c9d6e8" : "#fbfcfe",
          "fill-extrusion-height": ["get", "height"],
          "fill-extrusion-base": 0,
          "fill-extrusion-opacity": 0.92,
        },
      },
    ],
  };
}
