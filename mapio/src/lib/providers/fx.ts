import "server-only";
import { z } from "zod";
import { fxMode } from "@/lib/env";
import { fetchJson, fetchText } from "./http";
import { TtlCache } from "./cache";
import { recordUsage } from "./usage";
import { FIXTURE_FX } from "./fixtures";
import type { FxRates, ProviderMeta } from "./types";

/**
 * National Bank of Georgia official reference rates (not bank buy/sell rates).
 * Primary: NBG JSON gateway on nbg.gov.ge (structured: code, quantity, rate, validity date).
 * Secondary: the NBG currency web service (services.nbg.gov.ge/Rates/Service.asmx) for USD/EUR.
 * Both endpoints MUST be re-verified against NBG's current official documentation before
 * launch — see docs/providers.md. No HTML scraping.
 */
const NBG_JSON = "https://nbg.gov.ge/gw/api/ct/monetarypolicy/currencies/en/json/";
const NBG_ASMX = "https://services.nbg.gov.ge/Rates/Service.asmx";
const ATTR = "Official exchange rates: National Bank of Georgia";
const cache = new TtlCache<FxRates>(10);
const TTL = 60 * 60_000; // NBG publishes once per business day

const jsonSchema = z
  .array(
    z.object({
      date: z.string(),
      currencies: z.array(
        z.object({ code: z.string(), quantity: z.number(), rate: z.number(), name: z.string(), validFromDate: z.string().optional() }),
      ),
    }),
  )
  .min(1);

export function parseNbgJson(json: unknown): FxRates {
  const day = jsonSchema.parse(json)[0];
  return {
    publicationDate: (day.currencies[0]?.validFromDate ?? day.date).slice(0, 10),
    rates: day.currencies.map((c) => ({ code: c.code, quantity: c.quantity, rateGel: c.rate, name: c.name })),
  };
}

/** Parses `<double xmlns="...">2.7123</double>` style responses from the asmx service. */
export function parseAsmxNumber(xml: string): number | null {
  const m = xml.match(/>\s*(-?\d+(?:\.\d+)?)\s*</);
  return m ? Number(m[1]) : null;
}

async function loadNbg(): Promise<FxRates> {
  try {
    return parseNbgJson(await fetchJson(NBG_JSON, { timeoutMs: 6000, retries: 1 }));
  } catch {
    const codes = ["USD", "EUR"];
    const rates = await Promise.all(
      codes.map(async (code) => {
        const v = parseAsmxNumber(await fetchText(`${NBG_ASMX}/GetCurrency?currency=${code}`, { timeoutMs: 6000 }));
        if (v == null) throw new Error(`NBG asmx: unparsable ${code}`);
        return { code, quantity: 1, rateGel: v, name: code };
      }),
    );
    return { publicationDate: new Date().toISOString().slice(0, 10), rates };
  }
}

export async function getFxRates(userId: string | null): Promise<{ fx: FxRates; meta: ProviderMeta }> {
  if (fxMode() === "demo") {
    return {
      fx: FIXTURE_FX,
      meta: { source: "fixture", mode: "demo", fetchedAt: new Date().toISOString(), cached: false, attribution: "MAPIO demo values (not official rates)", notice: "Demo values — not official NBG rates." },
    };
  }
  try {
    const { value, cached, storedAt } = await cache.get("nbg", TTL, async () => {
      recordUsage("nbg", userId, "request");
      return loadNbg();
    });
    return {
      fx: value,
      meta: { source: "nbg", mode: "live", fetchedAt: new Date(storedAt).toISOString(), cached, attribution: ATTR, notice: "Official reference rates, not bank buy/sell rates." },
    };
  } catch (e) {
    recordUsage("nbg", userId, "error");
    const stale = cache.stale("nbg");
    if (stale) {
      return {
        fx: stale.value,
        meta: { source: "nbg", mode: "live", fetchedAt: new Date(stale.storedAt).toISOString(), cached: true, stale: true, attribution: ATTR, notice: `Rates from ${stale.value.publicationDate}; NBG currently unreachable.` },
      };
    }
    throw e;
  }
}
