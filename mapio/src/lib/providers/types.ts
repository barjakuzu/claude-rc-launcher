export type LngLat = [number, number];

export interface ProviderMeta {
  source: string; // e.g. "geoapify", "open-meteo", "nbg", "open-charge-map", "fixture"
  mode: "live" | "demo" | "free-tier";
  fetchedAt: string;
  cached: boolean;
  stale?: boolean;
  attribution: string;
  notice?: string; // human-readable caveat shown in UI
}

export interface Place {
  id: string;
  name: string;
  address: string;
  lat: number;
  lon: number;
  category: string | null;
  source: string;
}

export interface RouteStep {
  instruction: string;
  distanceM: number;
  durationS: number;
  maneuver: string | null;
  location: LngLat;
}
export interface Route {
  geometry: LngLat[];
  distanceM: number;
  durationS: number;
  steps: RouteStep[];
  /** Never true unless the provider explicitly returns licensed live traffic. */
  trafficAware: false;
}

export interface Weather {
  tempC: number;
  weatherCode: number;
  isDay: boolean;
  windKmh: number | null;
  todayMaxC: number | null;
  todayMinC: number | null;
  observedAt: string;
}

export interface FxRate {
  code: string;
  quantity: number;
  rateGel: number; // GEL per `quantity` units
  name: string;
}
export interface FxRates {
  publicationDate: string; // official NBG date the rates are valid for
  rates: FxRate[];
}

export type CurrentKind = "AC" | "DC" | "unknown";
export interface ChargerConnector {
  type: string;
  powerKw: number | null;
  current: CurrentKind;
  quantity: number | null;
}
export interface Charger {
  id: string;
  title: string;
  address: string;
  lat: number;
  lon: number;
  operator: string | null;
  connectors: ChargerConnector[];
  maxPowerKw: number | null;
  /** Reported operational status from the dataset — NOT live stall availability. */
  reportedStatus: string | null;
  statusUpdatedAt: string | null;
  /** Live occupancy is never available from the current sources. */
  liveAvailability: "unknown";
  dataProvider: string | null;
  license: string | null;
  distanceKm: number | null;
}
