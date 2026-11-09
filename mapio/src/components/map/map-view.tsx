"use client";

import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from "react";
import type { GeoJSONSource, Map as MlMap, Marker, StyleSpecification } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import type { MapClientConfig } from "@/lib/map/config";
import { fixtureStyle } from "@/lib/map/fixture-style";
import { MAPLIBRE_WORKER_URL } from "@/lib/map/worker-version";
import type { Charger, LngLat } from "@/lib/providers/types";

export interface MapViewHandle {
  flyTo(center: LngLat, zoom?: number): void;
  follow(center: LngLat, heading: number | null): void;
  fitRoute(geometry: LngLat[], padding?: { top: number; bottom: number; left: number; right: number }): void;
  setRoute(geometry: LngLat[] | null): void;
  setDestination(p: LngLat | null): void;
  setPuck(p: LngLat | null, heading: number | null, stale: boolean): void;
  setChargers(list: Charger[]): void;
  set3D(on: boolean): void;
  getCenter(): LngLat | null;
  resize(): void;
}

interface Props {
  config: MapClientConfig;
  theme: "dark" | "light";
  initialCenter: LngLat;
  initialZoom?: number;
  initial3D?: boolean;
  lowPerf?: boolean;
  interactive?: boolean;
  onClick?: (p: LngLat) => void;
  onLongPress?: (p: LngLat) => void;
  onUserPan?: () => void;
  onChargerClick?: (id: string) => void;
  onUnavailable?: (reason: string) => void;
  /** true while style/tile requests keep failing (provider down or key rejected); false once tiles load again. */
  onProviderError?: (down: boolean) => void;
  className?: string;
  ariaLabel?: string;
}

const ROUTE_SRC = "mapio-route";
const CHG_SRC = "mapio-chargers";
const BUILDINGS_ID = "mapio-buildings-3d";

function webglSupported() {
  try {
    const c = document.createElement("canvas");
    return !!(c.getContext("webgl2") || c.getContext("webgl"));
  } catch {
    return false;
  }
}

/** Adds a 3D building layer to vector styles that carry a `building` source-layer (OpenMapTiles schema). */
function ensureBuildings(map: MlMap, theme: "dark" | "light") {
  if (map.getLayer(BUILDINGS_ID)) return;
  const style = map.getStyle();
  const buildingLayer = style.layers.find((l) => "source-layer" in l && l["source-layer"] === "building");
  if (!buildingLayer || !("source" in buildingLayer)) return;
  // Hide flat building fills/extrusions from the base style; ours replaces them.
  for (const l of style.layers) {
    if ("source-layer" in l && l["source-layer"] === "building" && (l.type === "fill" || l.type === "fill-extrusion")) {
      map.setLayoutProperty(l.id, "visibility", "none");
    }
  }
  const firstSymbol = style.layers.find((l) => l.type === "symbol")?.id;
  map.addLayer(
    {
      id: BUILDINGS_ID,
      type: "fill-extrusion",
      source: buildingLayer.source as string,
      "source-layer": "building",
      minzoom: 14,
      paint: {
        "fill-extrusion-color": theme === "dark" ? "#c9d6e8" : "#f2f4f8",
        // Real heights where provided; modest fallback (8 m) where absent — shapes are approximate.
        "fill-extrusion-height": ["coalesce", ["get", "render_height"], ["get", "height"], 8],
        "fill-extrusion-base": ["coalesce", ["get", "render_min_height"], ["get", "min_height"], 0],
        "fill-extrusion-opacity": 0.9,
      },
    },
    firstSymbol,
  );
}

function addOverlays(map: MlMap) {
  if (!map.getSource(ROUTE_SRC)) {
    map.addSource(ROUTE_SRC, { type: "geojson", data: { type: "FeatureCollection", features: [] } });
    map.addLayer({
      id: "mapio-route-glow",
      type: "line",
      source: ROUTE_SRC,
      layout: { "line-cap": "round", "line-join": "round" },
      paint: {
        "line-color": "#4aa8ff",
        "line-opacity": 0.45,
        "line-blur": 8,
        "line-width": ["interpolate", ["linear"], ["zoom"], 10, 14, 18, 44],
      },
    });
    map.addLayer({
      id: "mapio-route-casing",
      type: "line",
      source: ROUTE_SRC,
      layout: { "line-cap": "round", "line-join": "round" },
      paint: { "line-color": "#0b4fc4", "line-width": ["interpolate", ["linear"], ["zoom"], 10, 8, 18, 30] },
    });
    map.addLayer({
      id: "mapio-route-line",
      type: "line",
      source: ROUTE_SRC,
      layout: { "line-cap": "round", "line-join": "round" },
      paint: { "line-color": "#2f8cff", "line-width": ["interpolate", ["linear"], ["zoom"], 10, 5.5, 18, 21] },
    });
  }
  if (!map.getSource(CHG_SRC)) {
    map.addSource(CHG_SRC, { type: "geojson", data: { type: "FeatureCollection", features: [] } });
    map.addLayer({
      id: "mapio-chargers",
      type: "circle",
      source: CHG_SRC,
      paint: {
        "circle-radius": ["interpolate", ["linear"], ["zoom"], 10, 6, 16, 12],
        "circle-color": ["case", ["==", ["get", "dc"], true], "#7c6cff", "#22b8f0"],
        "circle-stroke-color": "#ffffff",
        "circle-stroke-width": 2.5,
      },
    });
  }
}

export const MapView = forwardRef<MapViewHandle, Props>(function MapView(props, ref) {
  const { config, theme, initialCenter, initialZoom = 14, initial3D = true, lowPerf = false } = props;
  const el = useRef<HTMLDivElement>(null);
  const mapRef = useRef<MlMap | null>(null);
  const state = useRef({
    route: null as LngLat[] | null,
    chargers: [] as Charger[],
    is3D: initial3D,
    dest: null as Marker | null,
    puck: null as Marker | null,
  });
  const cbs = useRef(props);
  cbs.current = props;
  const [failed, setFailed] = useState<string | null>(null);
  const mlRef = useRef<typeof import("maplibre-gl") | null>(null);

  const styleFor = (t: "dark" | "light"): string | StyleSpecification =>
    config.provider === "fixture" ? fixtureStyle(t) : ((t === "dark" ? config.styleDark : config.styleLight) as string);

  // create map once
  useEffect(() => {
    if (!el.current) return;
    if (!webglSupported()) {
      setFailed("webgl");
      cbs.current.onUnavailable?.("webgl");
      return;
    }
    let disposed = false;
    let pressTimer: ReturnType<typeof setTimeout> | null = null;
    (async () => {
      const ml = await import("maplibre-gl");
      if (disposed || !el.current) return;
      if (ml.getWorkerUrl() !== MAPLIBRE_WORKER_URL) ml.setWorkerUrl(MAPLIBRE_WORKER_URL);
      mlRef.current = ml;
      const map = new ml.Map({
        container: el.current,
        style: styleFor(theme),
        center: initialCenter,
        zoom: initialZoom,
        pitch: initial3D ? 58 : 0,
        maxPitch: lowPerf ? 50 : 70,
        attributionControl: { compact: true },
        fadeDuration: lowPerf ? 0 : 200,
        pixelRatio: Math.min(window.devicePixelRatio || 1, lowPerf ? 1 : 2),
        canvasContextAttributes: { antialias: !lowPerf },
        interactive: props.interactive !== false,
      });
      mapRef.current = map;
      let failures = 0;
      map.on("error", (e) => {
        // tile/network errors are non-fatal; surface a banner if they keep happening
        console.warn("[map]", e.error?.message ?? e);
        if (++failures >= 3) cbs.current.onProviderError?.(true);
      });
      map.on("data", (e) => {
        if (failures && (e as { dataType?: string }).dataType === "source" && map.areTilesLoaded()) {
          failures = 0;
          cbs.current.onProviderError?.(false);
        }
      });
      map.on("style.load", () => {
        ensureBuildings(map, theme);
        if (map.getLayer(BUILDINGS_ID)) map.setLayoutProperty(BUILDINGS_ID, "visibility", state.current.is3D ? "visible" : "none");
        addOverlays(map);
        applyRoute(map, state.current.route);
        applyChargers(map, state.current.chargers);
      });
      map.on("click", (e) => {
        const hits = map.getLayer("mapio-chargers") ? map.queryRenderedFeatures(e.point, { layers: ["mapio-chargers"] }) : [];
        if (hits.length && hits[0].properties?.id) {
          cbs.current.onChargerClick?.(String(hits[0].properties.id));
          return;
        }
        cbs.current.onClick?.([e.lngLat.lng, e.lngLat.lat]);
      });
      map.on("contextmenu", (e) => cbs.current.onLongPress?.([e.lngLat.lng, e.lngLat.lat]));
      map.on("touchstart", (e) => {
        if (e.points.length !== 1) return;
        const ll: LngLat = [e.lngLat.lng, e.lngLat.lat];
        pressTimer = setTimeout(() => cbs.current.onLongPress?.(ll), 600);
      });
      const cancelPress = () => {
        if (pressTimer) clearTimeout(pressTimer);
        pressTimer = null;
      };
      map.on("touchend", cancelPress);
      map.on("touchmove", cancelPress);
      map.on("dragstart", (e) => {
        if ((e as { originalEvent?: unknown }).originalEvent) cbs.current.onUserPan?.();
      });
      map.on("mouseenter", "mapio-chargers", () => (map.getCanvas().style.cursor = "pointer"));
      map.on("mouseleave", "mapio-chargers", () => (map.getCanvas().style.cursor = ""));
      map.on("webglcontextlost", () => {
        setFailed("context");
        cbs.current.onUnavailable?.("context");
      });
    })().catch((e) => {
      console.error("[map] init failed", e);
      setFailed("init");
      cbs.current.onUnavailable?.("init");
    });
    return () => {
      disposed = true;
      mapRef.current?.remove();
      mapRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // theme switch → swap style (overlays are re-added on style.load)
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    map.setStyle(styleFor(theme));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [theme]);

  useImperativeHandle(ref, () => ({
    flyTo(center, zoom) {
      mapRef.current?.flyTo({ center, zoom: zoom ?? Math.max(mapRef.current.getZoom(), 15), essential: true, duration: lowPerf ? 0 : 1200 });
    },
    follow(center, heading) {
      const map = mapRef.current;
      if (!map) return;
      map.easeTo({
        center,
        bearing: state.current.is3D && heading != null ? heading : map.getBearing(),
        zoom: Math.max(map.getZoom(), 16),
        duration: lowPerf ? 0 : 800,
        padding: { top: 0, bottom: Math.round(map.getContainer().clientHeight * 0.3), left: 0, right: 0 },
      });
    },
    fitRoute(geometry, padding) {
      const map = mapRef.current;
      const ml = mlRef.current;
      if (!map || !ml || geometry.length < 2) return;
      const b = new ml.LngLatBounds(geometry[0], geometry[0]);
      geometry.forEach((p) => b.extend(p));
      map.fitBounds(b, { padding: padding ?? 80, duration: lowPerf ? 0 : 900, pitch: state.current.is3D ? 45 : 0, bearing: 0 });
    },
    setRoute(geometry) {
      state.current.route = geometry;
      if (mapRef.current?.isStyleLoaded()) applyRoute(mapRef.current, geometry);
    },
    setDestination(p) {
      const map = mapRef.current;
      const ml = mlRef.current;
      state.current.dest?.remove();
      state.current.dest = null;
      if (!map || !ml || !p) return;
      const node = document.createElement("div");
      node.setAttribute("aria-hidden", "true");
      node.innerHTML =
        '<svg width="40" height="52" viewBox="0 0 40 52"><path d="M20 51C20 51 38 30 38 18A18 18 0 0 0 2 18C2 30 20 51 20 51Z" fill="#ef4444" stroke="#fff" stroke-width="3"/><circle cx="20" cy="18" r="7" fill="#fff"/></svg>';
      state.current.dest = new ml.Marker({ element: node, anchor: "bottom" }).setLngLat(p).addTo(map);
    },
    setPuck(p, heading, stale) {
      const map = mapRef.current;
      const ml = mlRef.current;
      if (!map || !ml) return;
      if (!p) {
        state.current.puck?.remove();
        state.current.puck = null;
        return;
      }
      if (!state.current.puck) {
        const node = document.createElement("div");
        node.className = "mapio-puck";
        node.setAttribute("aria-hidden", "true");
        node.innerHTML =
          '<svg width="54" height="54" viewBox="0 0 54 54"><circle cx="27" cy="27" r="25" fill="rgba(47,140,255,0.18)"/><path d="M27 8 L41 42 L27 34 L13 42 Z" fill="#2f8cff" stroke="#fff" stroke-width="3" stroke-linejoin="round"/></svg>';
        state.current.puck = new ml.Marker({ element: node, rotationAlignment: "map", pitchAlignment: "map" }).setLngLat(p).addTo(map);
      }
      state.current.puck.setLngLat(p);
      state.current.puck.setRotation(heading ?? 0);
      state.current.puck.getElement().style.opacity = stale ? "0.45" : "1";
    },
    setChargers(list) {
      state.current.chargers = list;
      if (mapRef.current?.isStyleLoaded()) applyChargers(mapRef.current, list);
    },
    set3D(on) {
      state.current.is3D = on;
      const map = mapRef.current;
      if (!map) return;
      map.easeTo({ pitch: on ? 58 : 0, bearing: on ? map.getBearing() : 0, duration: lowPerf ? 0 : 600 });
      if (map.getLayer(BUILDINGS_ID)) map.setLayoutProperty(BUILDINGS_ID, "visibility", on ? "visible" : "none");
    },
    getCenter() {
      const c = mapRef.current?.getCenter();
      return c ? [c.lng, c.lat] : null;
    },
    resize() {
      mapRef.current?.resize();
    },
  }));

  if (failed) {
    return (
      <div role="status" className={`grid place-items-center bg-bg-2 p-6 text-center ${props.className ?? ""}`}>
        <div className="max-w-sm">
          <p className="text-lg font-semibold">Map can&apos;t be displayed on this browser</p>
          <p className="mt-2 text-sm text-muted">
            {failed === "webgl"
              ? "This browser doesn't support WebGL. Search and route details still work below."
              : "The map stopped rendering. Reload the page, or try 2D mode."}
          </p>
        </div>
      </div>
    );
  }
  // MapLibre's CSS forces `position: relative` on the container, so positioning classes go on a wrapper.
  return (
    <div className={props.className}>
      <div ref={el} role="region" aria-label={props.ariaLabel ?? "Map"} className="h-full w-full" />
    </div>
  );
});

function applyRoute(map: MlMap, geometry: LngLat[] | null) {
  const src = map.getSource(ROUTE_SRC) as GeoJSONSource | undefined;
  src?.setData({
    type: "FeatureCollection",
    features: geometry && geometry.length > 1 ? [{ type: "Feature", properties: {}, geometry: { type: "LineString", coordinates: geometry } }] : [],
  });
}

function applyChargers(map: MlMap, list: Charger[]) {
  const src = map.getSource(CHG_SRC) as GeoJSONSource | undefined;
  src?.setData({
    type: "FeatureCollection",
    features: list.map((c) => ({
      type: "Feature",
      properties: { id: c.id, dc: c.connectors.some((k) => k.current === "DC") },
      geometry: { type: "Point", coordinates: [c.lon, c.lat] },
    })),
  });
}
