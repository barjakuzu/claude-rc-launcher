"use client";

import { useEffect, useState } from "react";
import { Banknote, CloudSun } from "lucide-react";
import { formatTemp, type Units } from "@/lib/format";
import type { FxRates, ProviderMeta, Weather } from "@/lib/providers/types";

/** Weather + FX chips in the top bar. Each loads independently and fails quietly. */
export function TopChips({ flags, units, currency }: { flags: { weather: boolean; fx_rates: boolean }; units: Units; currency: string }) {
  const [w, setW] = useState<{ weather: Weather; meta: ProviderMeta } | null>(null);
  const [fx, setFx] = useState<{ fx: FxRates; meta: ProviderMeta } | null>(null);

  useEffect(() => {
    if (flags.weather) {
      const go = (lat: number, lon: number) =>
        fetch(`/api/weather?lat=${lat.toFixed(2)}&lon=${lon.toFixed(2)}`)
          .then((r) => (r.ok ? r.json() : null))
          .then(setW)
          .catch(() => {});
      go(41.72, 44.83); // Tbilisi default; precise location only used on the map
    }
    if (flags.fx_rates) {
      fetch("/api/fx")
        .then((r) => (r.ok ? r.json() : null))
        .then(setFx)
        .catch(() => {});
    }
  }, [flags.weather, flags.fx_rates]);

  const target = currency === "GEL" ? "USD" : currency;
  const rate = fx?.fx.rates.find((r) => r.code === target);

  return (
    <div className="hidden items-center gap-2 sm:flex">
      {w && (
        <span className="glass flex min-h-12 items-center gap-2 !rounded-full px-4 font-semibold" title={`${w.meta.attribution}${w.meta.mode === "demo" ? " — demo data" : ""}`}>
          <CloudSun className="h-6 w-6 text-[#ffd36b]" aria-hidden /> {formatTemp(w.weather.tempC, units.temperature)}
          {w.meta.mode === "demo" && <span className="text-[10px] font-bold uppercase text-warn">demo</span>}
        </span>
      )}
      {rate && (
        <span
          className="glass flex min-h-12 items-center gap-2 !rounded-full px-4 font-semibold"
          title={`${fx!.meta.attribution} · ${fx!.fx.publicationDate} · official reference rate, not a bank buy/sell rate`}
        >
          <Banknote className="h-5 w-5 text-accent-2" aria-hidden />
          {rate.quantity} {rate.code} = {rate.rateGel.toFixed(4)} ₾
          {fx!.meta.mode === "demo" && <span className="text-[10px] font-bold uppercase text-warn">demo</span>}
        </span>
      )}
    </div>
  );
}
