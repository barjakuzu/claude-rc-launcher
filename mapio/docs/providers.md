# Providers

Each provider sits behind an adapter in `src/lib/providers/*` with a live implementation and a labelled fixture. Every response carries `meta: { source, mode, fetchedAt, cached, stale?, attribution, notice? }`, and the UI shows it.

> These sites could not be reached from the build sandbox, so this doc is written from the request/response shapes the code expects. **Re-read each provider's current docs and terms before launch.**

| Provider | Used for | Adapter | Key exposure | Docs |
| --- | --- | --- | --- | --- |
| MapLibre GL JS 6 | rendering | `components/map/map-view.tsx` | — | https://maplibre.org/maplibre-gl-js/docs/ · 3D: https://maplibre.org/maplibre-gl-js/docs/examples/display-buildings-in-3d/ |
| MapTiler Cloud (commercial plan) | vector tiles/styles | `lib/map/config.ts` | **public** key — restrict by origin + quota in MapTiler | https://www.maptiler.com/cloud/pricing/ |
| Geoapify | autocomplete, reverse, routing | `lib/providers/geo.ts` | server only | https://apidocs.geoapify.com/docs/routing/api/ · https://www.geoapify.com/pricing/ |
| Open-Meteo (customer API) | weather | `lib/providers/weather.ts` | server only | https://open-meteo.com/en/pricing |
| National Bank of Georgia | official GEL rates | `lib/providers/fx.ts` | none | https://services.nbg.gov.ge/ |
| Open Charge Map | charger POIs | `lib/providers/chargers.ts` | server only (`X-API-Key`) | https://www.openchargemap.org/develop/api |
| Supabase | Postgres, Auth, Realtime | `lib/db`, `lib/auth`, `components/map/hooks.ts` | publishable key public; secret server-only | https://supabase.com/docs |
| Paddle Billing | subscriptions | `lib/billing` | client token public; API key + webhook secret server-only | see docs/billing.md |

## Notes per provider

**MapTiler**
- Default styles are `streets-v2-dark` and `streets-v2`; both are configurable.
- MAPIO adds its own `fill-extrusion` layer from the OpenMapTiles `building` source-layer and hides the style's own building layers.
- Heights come from `render_height`/`height`, with an **8 m fallback** when a building has no height. 3D is therefore approximate; the features page says so.
- Coverage in Tbilisi must be checked by eye with a real key.
- Dev-only fallback is OpenFreeMap (`MAP_STYLE_PROVIDER=openfreemap`). Use MapTiler commercial in production.

**Geoapify**
- Endpoints:
  - `GET /v1/geocode/autocomplete?format=json&lang=&bias=proximity:lon,lat&text=`
  - `GET /v1/geocode/reverse?format=json`
  - `GET /v1/routing?waypoints=lat,lon|lat,lon&mode=drive&details=instruction_details`
- The route parser expects a `MultiLineString` geometry and `legs[].steps[].{from_index,distance,time,instruction.text,type}`.
- Before launch, verify:
  - Georgian (`lang=ka`) results.
  - Turn-instruction quality in Tbilisi.
  - Commercial terms, including caching and storing results.
  - Quotas.
- **No traffic is claimed.**
- Results are cached for 10 minutes per (language, rounded bias, query).

**Open-Meteo**
- With `OPEN_METEO_API_KEY` set, MAPIO calls `customer-api.open-meteo.com` with `apikey=`.
- The free `api.open-meteo.com` endpoint is used only when `OPEN_METEO_ALLOW_FREE_TIER=true`, and never in production.
- Results are cached for 10 minutes per 0.1° tile. On failure, MAPIO serves the last value labelled **stale**.

**NBG**
- Primary source: `https://nbg.gov.ge/gw/api/ct/monetarypolicy/currencies/en/json/` (code, quantity, rate, validity date).
- Fallback: `https://services.nbg.gov.ge/Rates/Service.asmx/GetCurrency?currency=USD|EUR` (scalar XML).
- **Action:** confirm with NBG's official documentation that one of these endpoints is the sanctioned access method. If neither is, add a licensed FX provider behind the same `getFxRates()` interface with its own attribution.
- The UI always says these are “official reference rates, not bank buy/sell rates” and shows the publication date. There is no HTML scraping.

**Open Charge Map**
- Request: `GET /v3/poi?output=json&compact=false&verbose=false&latitude&longitude&distance&distanceunit=KM&maxresults=100`.
- OCM provides POIs and a *reported operational status*, not live occupancy. MAPIO always shows **“Live availability: Unknown”** and never counts stalls.
- Each charger shows its data provider and licence. Review licences per record before redistributing data.
- Results are cached for 30 minutes per 0.05° tile.

## Quotas and cost
- `api_usage_daily` records requests, cache hits, and errors per provider per user per day.
- `DAILY_QUOTA` in `lib/providers/usage.ts` holds conservative alert thresholds. `/admin` highlights providers at 80% or more.
- `COST_MICROS` is 0 until you enter your contracted per-request prices; it then drives the per-active-user cost view.
- Caches and rate limits are per instance. Use a shared store (Upstash/Redis) for multi-region deployments.
