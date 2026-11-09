/**
 * Provider PARSER tests against response-shape FIXTURES (not live calls).
 * Live provider behaviour must be verified separately with real keys (docs/manual-qa.md).
 */
import { describe, expect, it } from "vitest";
import { parseGeoapifyRoute } from "@/lib/providers/geo";
import { parseOpenMeteo } from "@/lib/providers/weather";
import { parseAsmxNumber, parseNbgJson } from "@/lib/providers/fx";
import { filterChargers, parseOcm } from "@/lib/providers/chargers";
import { fixtureSearch } from "@/lib/providers/fixtures";

describe("Geoapify routing parser (fixture shape)", () => {
  const json = {
    features: [
      {
        geometry: { type: "MultiLineString", coordinates: [[[44.8015, 41.6934], [44.805, 41.695], [44.8166, 41.6974]]] },
        properties: {
          distance: 1450,
          time: 240,
          legs: [
            {
              steps: [
                { from_index: 0, to_index: 1, distance: 400, time: 70, instruction: { text: "Drive northeast on Rustaveli Avenue.", type: "StartAt" } },
                { from_index: 1, to_index: 2, distance: 1050, time: 170, instruction: { text: "Turn right onto Baratashvili Street.", type: "Right" } },
                { from_index: 2, to_index: 2, distance: 0, time: 0, instruction: { text: "You have arrived.", type: "Finish" } },
              ],
            },
          ],
        },
      },
    ],
  };
  it("extracts geometry, totals and steps with locations", () => {
    const r = parseGeoapifyRoute(json);
    expect(r.geometry).toHaveLength(3);
    expect(r.distanceM).toBe(1450);
    expect(r.durationS).toBe(240);
    expect(r.steps.map((s) => s.maneuver)).toEqual(["StartAt", "Right", "Finish"]);
    expect(r.steps[1].location).toEqual([44.805, 41.695]);
    expect(r.trafficAware).toBe(false);
  });
  it("rejects malformed responses", () => {
    expect(() => parseGeoapifyRoute({ features: [] })).toThrow();
  });
});

describe("Open-Meteo parser (fixture shape)", () => {
  it("parses current + daily", () => {
    const w = parseOpenMeteo({
      current: { time: "2026-11-09T12:00", temperature_2m: 14.2, weather_code: 3, wind_speed_10m: 11, is_day: 1 },
      daily: { temperature_2m_max: [17.1], temperature_2m_min: [6.4] },
    });
    expect(w).toMatchObject({ tempC: 14.2, weatherCode: 3, isDay: true, todayMaxC: 17.1, todayMinC: 6.4 });
    expect(w.observedAt).toBe("2026-11-09T12:00:00.000Z");
  });
});

describe("NBG parsers (fixture shape)", () => {
  it("parses the JSON gateway with quantity and validity date", () => {
    const fx = parseNbgJson([
      {
        date: "2026-11-09T00:00:00.000Z",
        currencies: [
          { code: "USD", quantity: 1, rate: 2.7123, name: "US Dollar", validFromDate: "2026-11-10T00:00:00.000Z" },
          { code: "TRY", quantity: 10, rate: 0.7812, name: "Turkish Lira" },
        ],
      },
    ]);
    expect(fx.publicationDate).toBe("2026-11-10");
    expect(fx.rates[1]).toEqual({ code: "TRY", quantity: 10, rateGel: 0.7812, name: "Turkish Lira" });
  });
  it("parses asmx scalar XML", () => {
    expect(parseAsmxNumber('<?xml version="1.0"?><double xmlns="http://tempuri.org/">2.7123</double>')).toBe(2.7123);
    expect(parseAsmxNumber("<html>error</html>")).toBeNull();
  });
});

describe("Open Charge Map parser (fixture shape)", () => {
  const raw = [
    {
      ID: 1,
      AddressInfo: { Title: "Site A", AddressLine1: "1 Test St", Town: "Tbilisi", Latitude: 41.7, Longitude: 44.8 },
      OperatorInfo: { Title: "Operator X" },
      StatusType: { Title: "Operational", IsOperational: true },
      DateLastStatusUpdate: "2026-10-01T00:00:00Z",
      DataProvider: { Title: "Open Charge Map Contributors", License: "CC BY 4.0" },
      Connections: [
        { ConnectionType: { Title: "CCS (Type 2)" }, CurrentType: { Title: "DC" }, PowerKW: 150, Quantity: 2 },
        { ConnectionType: { Title: "Type 2 (Socket Only)" }, CurrentType: { Title: "AC (Three-Phase)" }, PowerKW: 22, Quantity: 1 },
      ],
    },
    { ID: 2, AddressInfo: { Title: "Site B", Latitude: 41.71, Longitude: 44.81 }, Connections: [] },
  ];
  it("maps connectors, power, attribution — and never claims live availability", () => {
    const list = parseOcm(raw, { lat: 41.7, lon: 44.8 });
    expect(list[0]).toMatchObject({ maxPowerKw: 150, operator: "Operator X", license: "CC BY 4.0", liveAvailability: "unknown", reportedStatus: "Operational" });
    expect(list[0].connectors.map((c) => c.current)).toEqual(["DC", "AC"]);
    expect(list.every((c) => c.liveAvailability === "unknown")).toBe(true);
    expect(list[1].maxPowerKw).toBeNull();
  });
  it("filters by current and min power", () => {
    const list = parseOcm(raw, { lat: 41.7, lon: 44.8 });
    expect(filterChargers(list, { current: "DC" }).map((c) => c.id)).toEqual(["ocm_1"]);
    expect(filterChargers(list, { minKw: 200 })).toHaveLength(0);
    expect(filterChargers(list, { connector: "type 2" })).toHaveLength(1);
  });
});

describe("demo search fixtures", () => {
  it("finds places in English and Georgian", () => {
    expect(fixtureSearch("freedom", "en")[0].name).toBe("Freedom Square");
    expect(fixtureSearch("თავისუფლების", "ka")[0].name).toBe("თავისუფლების მოედანი");
    expect(fixtureSearch("airport", "ka")[0].name).toBe("თბილისის საერთაშორისო აეროპორტი");
  });
});
