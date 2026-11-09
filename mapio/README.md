# MAPIO

Premium 3D navigation web app for large in-car browsers (Tesla-browser friendly), launching
in Georgia/Tbilisi. Subscription SaaS: **MAPIO Premium**, monthly with a 7-day free trial.

> MAPIO is independent and not affiliated with Tesla, Inc. It does not connect to vehicle systems.

## Quick start (no credentials needed)

```bash
cd mapio
npm install
npm run dev            # http://localhost:3000 — every provider runs in labelled demo mode
```

In development, with no environment variables set:

| Area | Demo adapter |
| --- | --- |
| Database | Embedded **PGlite** at `.data/pglite`, running the real Supabase migrations and RLS |
| Auth | Local email/password with scrypt hashes and an HMAC-signed cookie |
| Billing | **Billing simulator** that emits *signed* Paddle-shaped webhooks through the real webhook pipeline |
| Map | OpenFreeMap dev tiles (`MAP_STYLE_PROVIDER=fixture` → offline synthetic city) |
| Search / routing / weather / chargers | Labelled fixtures. Routes are straight-line approximations marked “Demo route” |
| FX | Live NBG (public). `FX_PROVIDER=fixture` for offline |

Try the whole flow: sign up → onboarding → **Start 7-day free trial** → *Simulate successful checkout* →
map search (“Freedom”, “Narikala”, “თავისუფლების”) → Directions → Start. For Send to Car, open
`/app/devices` in a second browser profile, pair it, and send from the first one.

**Free public link (on your machine):** `npm run tunnel` builds the app, starts it in demo mode and prints a
`https://<random>.trycloudflare.com` URL via Cloudflare's free quick tunnel (requires `cloudflared`). The link
changes each run and only works while your computer is on.

To go live, fill in `.env.example` (each variable says where to get it) — see `docs/deployment.md`.

## Commands

| Command | What it does |
| --- | --- |
| `npm run dev` | Dev server (copies the MapLibre worker into `public/vendor` first) |
| `npm run build` / `npm start` | Production build / server |
| `npm run typecheck` · `npm run lint` | TypeScript strict · ESLint (incl. React Compiler rules) |
| `npm test` | Vitest unit + integration (PGlite: migrations, RLS, webhooks, pairing) |
| `npm run test:e2e` | Playwright against a production build in demo mode (`.env.e2e`). Uses its own browsers; set `PW_CHROMIUM_PATH` to reuse a system Chromium |
| `npm run check` | typecheck + lint + unit/integration + build |
| `npm run preview:capture` | Regenerates the landing-page map render from the app itself |

## Project structure

```
src/app/(marketing)   landing, pricing, features, how-it-works, faq, terms, privacy, contact
src/app/(auth)        login, signup, forgot/reset password (+ server actions)
src/app/(app)         /app dashboard, map, chargers, saved, devices, inbox · /account, /account/billing · /admin
src/app/drive         lean full-screen driving UI (no account panels)
src/app/pair/[token]  phone-side pairing approval
src/app/dev/billing   billing simulator (demo mode only; 404 otherwise)
src/app/api           typed route handlers (geo, weather, fx, chargers, devices, inbox, places, billing, webhooks, cron, admin)
src/lib/db            SQL layer: Supabase Postgres (pg) or PGlite; withUser() runs queries under RLS
src/lib/auth          Supabase Auth adapter + dev adapter
src/lib/billing       entitlement rules, Paddle signature, webhook processor, Paddle API, simulator
src/lib/providers     Geoapify, Open-Meteo, NBG, Open Charge Map adapters + fixtures, cache, usage metering
src/lib/repos         data access per domain (profiles, places, devices/pairing, inbox, admin)
src/components/map    MapLibre MapView, Navigator (search/route/guidance), geolocation + inbox hooks
supabase/migrations   schema + RLS (source of truth)        supabase/seed.sql  dev fixtures
tests/unit · tests/integration · tests/e2e
docs/                 architecture, providers, billing, deployment, tesla-browser-testing, legal checklist, manual QA
```

## Features and limitations

**Works now:**
- Landing, pricing, and legal-draft pages.
- Auth, with route protection.
- Paywall gated by entitlement. Access comes only from verified webhooks.
- 3D map with 2D toggle, search in English and Georgian, route preview, and turn-by-turn guidance from GPS.
- Off-route reroute and opt-in voice.
- Chargers with filters and attribution, weather, and NBG rates.
- Saved and recent places.
- QR pairing and Send to Car, with Realtime + polling and replay after reconnect.
- Billing: trial, cancel at period end, undo, Paddle portal, and reconciliation cron.
- Admin console with aggregates, provider health, feature flags, and audited access grants.
- Data export and account deletion.

**Honest limits:**
- No live traffic, lane guidance, or live charger availability. Availability always shows as *unknown*.
- No vehicle data or control. Send to Car reaches **MAPIO in the car's browser**, not the car's built-in navigation.
- Guidance depends on what the browser exposes (GPS, heading, background tabs). It is still **unverified on a real Tesla MCU**; see `docs/tesla-browser-testing.md`.
- Live provider adapters are written against documented response shapes, but **have not been exercised with real keys** from this environment. See `STATUS.md`.

See `STATUS.md` for exact status and launch blockers.
