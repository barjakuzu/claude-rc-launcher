"use client";

import dynamic from "next/dynamic";
import { useEffect, useRef, useState } from "react";
import type { MapClientConfig } from "@/lib/map/config";
import type { LngLat } from "@/lib/providers/types";
import type { MapViewHandle } from "./map-view";
import { useDomTheme } from "@/components/hooks/client-prefs";

const MapView = dynamic(() => import("./map-view").then((m) => m.MapView), {
  ssr: false,
  loading: () => <div className="absolute inset-0 animate-pulse bg-bg-2" />,
});

/** Non-interactive 3D preview for the dashboard; lazily loads MapLibre after first paint. */
export function MiniMap({ config, center, pins = [] }: { config: MapClientConfig; center: LngLat; pins?: LngLat[] }) {
  const ref = useRef<MapViewHandle>(null);
  const theme = useDomTheme();
  const [show, setShow] = useState(false);
  useEffect(() => {
    const id = requestIdleCallback ? requestIdleCallback(() => setShow(true)) : (setTimeout(() => setShow(true), 300) as unknown as number);
    return () => (cancelIdleCallback ? cancelIdleCallback(id) : clearTimeout(id));
  }, []);
  useEffect(() => {
    if (!show || !pins[0]) return;
    const t = setTimeout(() => ref.current?.setDestination(pins[0]), 800);
    return () => clearTimeout(t);
  }, [show, pins]);
  return (
    <div className="relative h-full min-h-[260px] overflow-hidden rounded-[18px]">
      {show && <MapView ref={ref} config={config} theme={theme} initialCenter={center} initialZoom={15} interactive={false} className="absolute inset-0" ariaLabel="Map preview" />}
    </div>
  );
}
