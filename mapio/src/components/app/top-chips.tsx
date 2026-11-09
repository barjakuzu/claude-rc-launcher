"use client";

import { useEffect, useState } from "react";
import { Banknote, CloudSun } from "lucide-react";
import { formatTemp, type Units } from "@/lib/format";
import type { FxRates, ProviderMeta, Weather } from "@/lib/providers/types";

/**
 * Weather + official FX chips. `header` shows them in the top bar from md up; `inline`
 * shows them as a compact row on small screens (dashboard), so the header never crowds.
 */
export function TopChips({
  flags,
  units,
  currency,
  variant = "header",
}: {
  flags: { weather: boolean; fx_rates: boolean };
  units: Units;
  currency: string;
  variant?: "header" | "inline";
}) {
  const [w, setW] = useState<{ weather: Weather; meta: ProviderMeta } | null>(null);
  const [fx, setFx] = useState<{ fx: FxRates; meta: ProviderMeta } | null>(null);

  useEffect(() => {
    if (flags.weather) {
      // Tbilisi area; precise location is only used on the map.
      fetch(`/api/weather?lat=41.72&lon=44.83`)
        .then((r) => (r.ok ? r.json() : null))
        .then(setW)
        .catch(() => {});
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
  const header = variant === "header";
  const chip = header ? "glass flex h-12 items-center gap-2 !rounded-full px-4 text-[15px] font-semibold" : "chip !py-1.5 text-[13px]";
  const demo = <span className="text-[10px] font-bold uppercase tracking-wide text-warn">demo</span>;

  if (!w && !rate) return null;
  return (
    <div className={header ? "hidden items-center gap-2 whitespace-nowrap md:flex" : "flex flex-wrap items-center gap-2 md:hidden"}>
      {w && (
        <span className={chip} title={`${w.meta.attribution}${w.meta.mode === "demo" ? " (demo data)" : ""}`}>
          <CloudSun className={header ? "h-6 w-6 text-[#ffd36b]" : "h-4 w-4 text-[#ffd36b]"} aria-hidden />
          {formatTemp(w.weather.tempC, units.temperature)}
          {w.meta.mode === "demo" && demo}
        </span>
      )}
      {rate && (
        <span
          className={chip}
          title={`${fx!.meta.attribution} · ${fx!.fx.publicationDate} · official reference rate, not a bank buy/sell rate`}
        >
          <Banknote className={header ? "h-5 w-5 text-accent-2" : "h-4 w-4 text-accent-2"} aria-hidden />
          {rate.quantity} {rate.code} = {rate.rateGel.toFixed(4)} ₾{fx!.meta.mode === "demo" && demo}
        </span>
      )}
    </div>
  );
}
