# MAPIO status

_Last updated: 2026-11-09 (UI redesign)._

## Verified in this environment
| Check | Result |
| --- | --- |
| `npm run typecheck` | ✅ clean (TS strict) |
| `npm run lint` | ✅ clean, including React Compiler rules |
| `npm test` (Vitest) | ✅ 55 tests. Unit tests: entitlement, Paddle signature (also cross-checked against the official SDK), nav math, provider parsers. Integration tests on PGlite with the real migrations: RLS isolation, role-escalation denial, webhook lifecycle (dup / out-of-order / forged), QR pairing replay / expiry / cross-account, inbox replay / revoke |
| `npm run build` | ✅ |
| `npm run test:e2e` | ✅ 19 tests on desktop, 1280×800 landscape, and phone (Pixel 7). Covers the full vertical slice, pairing + Send to Car + reconnect replay + revoke, API protection (401 / 402 / 403 / CSRF / open-redirect / webhook signature), and the no-GPS behaviour |

E2E runs in **demo mode**: fixture map/search/routing, billing simulator, local auth, and PGlite.

## UI redesign (premium glass)
- Applied across the landing page, auth pages, dashboard, map and drive mode, billing, devices and inbox. See docs/design-system.md.
- Visually checked with `scripts/visual-snapshots.mjs` at 1440×900, 1280×800, 1920×1200, 390×844 and 375×667:
  - no horizontal overflow
  - no phone touch targets under 40 px
  - the mobile dock clears content and the iOS safe area
- Not verified on a physical iPhone or Tesla screen. Real backdrop-blur performance on car hardware is unknown; "Reduce effects" exists for that.

## Implemented but NOT verified against real services
These adapters follow the providers' documented request and response shapes, and their parsers are unit-tested against fixture payloads. This sandbox could not reach the providers, so none has run against a live service yet:
- **Supabase Auth / Realtime / Postgres:** `pg` driver against the pooler; cookie sessions via `@supabase/ssr`.
- **Paddle Billing (sandbox):** transactions, overlay checkout, webhooks, portal sessions, cancel/undo, `GET /prices`, reconciliation.
- **MapTiler styles and 3D building coverage in Tbilisi.**
- **Geoapify** autocomplete, reverse geocoding, and routing, including Georgian-language result quality.
- **Open-Meteo** customer API.
- **NBG:** JSON gateway, with an asmx fallback. The endpoints still need to be re-confirmed against NBG's official docs (see docs/providers.md).
- **Open Charge Map** with `compact=false`.

## External blockers (need you)
See the checklist in `docs/deployment.md`:
- MapTiler, Geoapify, Open-Meteo, and OCM keys.
- A Supabase project.
- Paddle seller approval, sandbox keys, and a price with a 7-day trial.
- Vercel and domain setup.
- An email provider.
- Approved legal documents.
- A real-vehicle browser test.

## Known limitations and decisions
- **In-memory limits:** rate limits and provider caches are per instance. Move them to a shared store (Redis/Upstash) before scaling out.
- **Dev password reset:** the dev adapter shows the reset link on screen because there's no mailer. Live mode uses Supabase email.
- **Voice guidance:** uses the Web Speech API and is opt-in. It may be unavailable in some car browsers.
- **Guidance needs a live position:** turn-by-turn guidance runs only with a fresh GPS fix. Without one, the UI shows a route preview and a "Waiting for GPS…" state. Behaviour on a real Tesla browser is still unknown.
- **No analytics or Sentry yet:** neither is wired in. The logging points are `console.error` in `lib/api.ts`. See docs/architecture.md for the adapter seam.
