/**
 * DEMO FIXTURES — used only when the matching live provider is not configured.
 * Place coordinates are approximate positions of well-known public landmarks.
 * Routes, weather, FX values and chargers below are SYNTHETIC and are labelled as
 * demo data everywhere they are shown. They must never be presented as real.
 */
import type { Charger, FxRates, LngLat, Place, Route, RouteStep, Weather } from "./types";

export const TBILISI: { lat: number; lon: number } = { lat: 41.7151, lon: 44.8271 };

const P = (id: string, en: string, ka: string, lat: number, lon: number, cat: string, area: string) => ({
  id: `fx_${id}`,
  en,
  ka,
  lat,
  lon,
  cat,
  area,
});
export const FIXTURE_PLACES = [
  P("freedom_sq", "Freedom Square", "თავისუფლების მოედანი", 41.6934, 44.8015, "landmark", "Old Tbilisi, Tbilisi"),
  P("rustaveli", "Rustaveli Avenue", "რუსთაველის გამზირი", 41.699, 44.7975, "street", "Mtatsminda, Tbilisi"),
  P("peace_bridge", "Bridge of Peace", "მშვიდობის ხიდი", 41.6929, 44.8086, "landmark", "Old Tbilisi, Tbilisi"),
  P("narikala", "Narikala Fortress", "ნარიყალას ციხე", 41.688, 44.8086, "landmark", "Old Tbilisi, Tbilisi"),
  P("airport", "Tbilisi International Airport", "თბილისის საერთაშორისო აეროპორტი", 41.6692, 44.9547, "airport", "Samgori, Tbilisi"),
  P("vake_park", "Vake Park", "ვაკის პარკი", 41.7069, 44.752, "park", "Vake, Tbilisi"),
  P("mtatsminda", "Mtatsminda Park", "მთაწმინდის პარკი", 41.6943, 44.7867, "park", "Mtatsminda, Tbilisi"),
  P("station", "Tbilisi Central Railway Station", "თბილისის ცენტრალური სადგური", 41.7213, 44.7985, "transport", "Didube-Chughureti, Tbilisi"),
  P("sameba", "Holy Trinity Cathedral", "სამების საკათედრო ტაძარი", 41.6974, 44.8166, "landmark", "Avlabari, Tbilisi"),
  P("dry_bridge", "Dry Bridge Market", "მშრალი ხიდი", 41.7003, 44.8058, "market", "Old Tbilisi, Tbilisi"),
  P("lisi", "Lisi Lake", "ლისის ტბა", 41.7466, 44.743, "park", "Saburtalo, Tbilisi"),
  P("tbilisi_sea", "Tbilisi Sea", "თბილისის ზღვა", 41.7615, 44.8227, "park", "Gldani, Tbilisi"),
  P("jvari", "Jvari Monastery", "ჯვრის მონასტერი", 41.8383, 44.7336, "landmark", "Mtskheta"),
  P("kutaisi", "Kutaisi", "ქუთაისი", 42.2679, 42.6946, "city", "Imereti"),
  P("batumi", "Batumi Boulevard", "ბათუმის ბულვარი", 41.651, 41.6305, "landmark", "Batumi, Adjara"),
  P("gudauri", "Gudauri", "გუდაური", 42.4777, 44.4786, "town", "Mtskheta-Mtianeti"),
];

export function fixtureSearch(q: string, lang: "en" | "ka", limit = 8): Place[] {
  const needle = q.trim().toLowerCase();
  if (!needle) return [];
  return FIXTURE_PLACES.filter((p) => p.en.toLowerCase().includes(needle) || p.ka.includes(needle) || p.area.toLowerCase().includes(needle))
    .slice(0, limit)
    .map((p) => ({
      id: p.id,
      name: lang === "ka" ? p.ka : p.en,
      address: p.area,
      lat: p.lat,
      lon: p.lon,
      category: p.cat,
      source: "fixture",
    }));
}

export function fixtureReverse(lat: number, lon: number): Place {
  const nearest = [...FIXTURE_PLACES].sort((a, b) => haversineM([lon, lat], [a.lon, a.lat]) - haversineM([lon, lat], [b.lon, b.lat]))[0];
  return {
    id: `fx_pin_${lat.toFixed(5)}_${lon.toFixed(5)}`,
    name: "Dropped pin",
    address: `Near ${nearest.en} (demo reverse geocoding)`,
    lat,
    lon,
    category: null,
    source: "fixture",
  };
}

export function haversineM(a: LngLat, b: LngLat): number {
  const R = 6371000;
  const toRad = (d: number) => (d * Math.PI) / 180;
  const dLat = toRad(b[1] - a[1]);
  const dLon = toRad(b[0] - a[0]);
  const s = Math.sin(dLat / 2) ** 2 + Math.cos(toRad(a[1])) * Math.cos(toRad(b[1])) * Math.sin(dLon / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(s));
}

/** Synthetic L-shaped route. Clearly not road-snapped; UI labels it "demo route". */
export function fixtureRoute(from: LngLat, to: LngLat): Route {
  const corner: LngLat = [from[0], to[1]];
  const legs: LngLat[] = [];
  const steps = 12;
  for (let i = 0; i <= steps; i++) legs.push([from[0], from[1] + ((to[1] - from[1]) * i) / steps]);
  for (let i = 1; i <= steps; i++) legs.push([corner[0] + ((to[0] - corner[0]) * i) / steps, to[1]]);
  const d1 = haversineM(from, corner);
  const d2 = haversineM(corner, to);
  const speed = 30 / 3.6; // 30 km/h demo average
  const goingNorth = to[1] > from[1];
  const goingEast = to[0] > from[0];
  const turn = goingNorth === goingEast ? "right" : "left";
  const s: RouteStep[] = [
    { instruction: `Head ${goingNorth ? "north" : "south"} (demo route)`, distanceM: d1, durationS: d1 / speed, maneuver: "depart", location: from },
    { instruction: `Turn ${turn} (demo route)`, distanceM: d2, durationS: d2 / speed, maneuver: `turn-${turn}`, location: corner },
    { instruction: "Arrive at your destination", distanceM: 0, durationS: 0, maneuver: "arrive", location: to },
  ];
  return { geometry: legs, distanceM: d1 + d2, durationS: (d1 + d2) / speed, steps: s, trafficAware: false };
}

export function fixtureWeather(lat: number): Weather {
  const day = new Date();
  const month = day.getUTCMonth();
  const seasonal = [2, 4, 9, 14, 19, 24, 27, 27, 22, 15, 9, 4][month];
  const temp = Math.round(seasonal - (lat - 41.7) * 2);
  return {
    tempC: temp,
    weatherCode: 2,
    isDay: day.getUTCHours() + 4 >= 7 && day.getUTCHours() + 4 <= 19,
    windKmh: 9,
    todayMaxC: temp + 3,
    todayMinC: temp - 6,
    observedAt: day.toISOString(),
  };
}

export const FIXTURE_FX: FxRates = {
  publicationDate: "demo",
  rates: [
    { code: "USD", quantity: 1, rateGel: 2.7, name: "US Dollar" },
    { code: "EUR", quantity: 1, rateGel: 2.95, name: "Euro" },
    { code: "GBP", quantity: 1, rateGel: 3.45, name: "Pound Sterling" },
    { code: "TRY", quantity: 10, rateGel: 0.78, name: "Turkish Lira" },
    { code: "AMD", quantity: 1000, rateGel: 6.9, name: "Armenian Dram" },
  ],
};

export function fixtureChargers(lat: number, lon: number): Charger[] {
  const offsets: [number, number, number | null, "AC" | "DC", string][] = [
    [0.004, 0.006, 150, "DC", "CCS2"],
    [-0.006, 0.002, 22, "AC", "Type 2"],
    [0.011, -0.009, 50, "DC", "CHAdeMO"],
    [-0.014, -0.012, 11, "AC", "Type 2"],
    [0.019, 0.015, null, "unknown" as "AC", "Unknown"],
  ];
  return offsets.map(([dLat, dLon, kw, cur, type], i) => ({
    id: `fx_chg_${i + 1}`,
    title: `Demo charger ${i + 1}`,
    address: "Synthetic location (demo data)",
    lat: lat + dLat,
    lon: lon + dLon,
    operator: null,
    connectors: [{ type, powerKw: kw, current: cur, quantity: null }],
    maxPowerKw: kw,
    reportedStatus: null,
    statusUpdatedAt: null,
    liveAvailability: "unknown",
    dataProvider: "MAPIO demo fixtures",
    license: null,
    distanceKm: haversineM([lon, lat], [lon + dLon, lat + dLat]) / 1000,
  }));
}
