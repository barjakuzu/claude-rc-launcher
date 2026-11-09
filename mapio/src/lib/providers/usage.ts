import "server-only";
import { withService } from "@/lib/db";

/** Rough per-request cost assumptions (micro-USD) for quota/cost dashboards. Tune in docs/providers.md. */
const COST_MICROS: Record<string, number> = {
  geoapify_autocomplete: 0,
  geoapify_routing: 0,
  geoapify_reverse: 0,
  open_meteo: 0,
  open_charge_map: 0,
  nbg: 0,
  paddle: 0,
};

/** Daily quota alerts (requests/day across all users) — conservative defaults; adjust per plan. */
export const DAILY_QUOTA: Record<string, number> = {
  geoapify_autocomplete: 2500,
  geoapify_routing: 1500,
  geoapify_reverse: 1000,
  open_meteo: 10000,
  open_charge_map: 5000,
};

/** Fire-and-forget usage counter; never blocks or fails the user request. */
export function recordUsage(provider: string, userId: string | null, kind: "request" | "cache_hit" | "error") {
  const col = kind === "request" ? "requests" : kind === "cache_hit" ? "cache_hits" : "errors";
  const cost = kind === "request" ? (COST_MICROS[provider] ?? 0) : 0;
  void withService((tx) =>
    tx.query(
      `insert into public.api_usage_daily (day, provider, user_key, ${col}, approx_cost_micros)
       values (current_date, $1, $2, 1, $3)
       on conflict (day, provider, user_key) do update
       set ${col} = public.api_usage_daily.${col} + 1,
           approx_cost_micros = public.api_usage_daily.approx_cost_micros + $3`,
      [provider, userId ?? "anon", cost],
    ),
  ).catch(() => {});
}
