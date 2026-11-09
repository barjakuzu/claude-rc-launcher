import "server-only";
import { env, mapStyleProvider } from "@/lib/env";

export interface MapClientConfig {
  provider: "maptiler" | "openfreemap" | "fixture";
  styleDark: string | null; // style URL; null => client builds the offline fixture style
  styleLight: string | null;
  demo: boolean;
  notice: string | null;
}

/**
 * Public map configuration (safe to send to the browser).
 * MapTiler keys are necessarily public: restrict them by HTTP origin in the MapTiler
 * dashboard and set quotas (docs/providers.md).
 */
export function mapClientConfig(): MapClientConfig {
  const p = mapStyleProvider();
  const e = env();
  if (p === "maptiler") {
    const u = (style: string) => `https://api.maptiler.com/maps/${style}/style.json?key=${e.NEXT_PUBLIC_MAPTILER_KEY}`;
    return { provider: p, styleDark: u(e.MAPTILER_STYLE_DARK), styleLight: u(e.MAPTILER_STYLE_LIGHT), demo: false, notice: null };
  }
  if (p === "openfreemap") {
    return {
      provider: p,
      styleDark: "https://tiles.openfreemap.org/styles/dark",
      styleLight: "https://tiles.openfreemap.org/styles/liberty",
      demo: true,
      notice: "Development tiles (OpenFreeMap). Configure MapTiler for production.",
    };
  }
  return {
    provider: "fixture",
    styleDark: null,
    styleLight: null,
    demo: true,
    notice: "Offline demo map — synthetic streets and buildings, not real geography.",
  };
}
