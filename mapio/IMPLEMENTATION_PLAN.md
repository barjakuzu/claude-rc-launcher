# MAPIO implementation plan

Status key: ✅ done · 🟡 implemented, needs external verification · ⛔ blocked on external input

## Milestone A — scaffold, design system, marketing, auth screens ✅
- Next.js 16 App Router + TS strict + Tailwind 4; Cache Components off (see architecture).
- Design tokens (dark midnight glass + light mode), glass/btn/field/chip primitives, MAPIO mark.
- Landing modelled on the reference (hero, tilted 3D preview from a real app render, trust bar,
  6 feature tiles, pricing card, subscription illustration, FAQ), /pricing, /features, /how-it-works,
  /faq, /terms, /privacy, /contact (legal = marked drafts), /login, /signup, /forgot-password,
  /reset-password, /auth/callback, /onboarding. SEO: metadata, OG, sitemap, robots, JSON-LD.

## Milestone B — data model, auth, RLS, account ✅
- `supabase/migrations/…_init.sql`: profiles, subscriptions, processed_billing_events, trial_claims,
  paired_devices, device_pairing_requests, saved_places, recent_destinations, inbox_destinations,
  api_usage_daily, admin_audit_log, feature_flags, access_grants. RLS on every table; column grants
  stop role escalation; entitlement tables writable by the service role only.
- Server queries run as `authenticated` with JWT claims set (`withUser`) → RLS applies server-side.
- Supabase Auth adapter (cookies via @supabase/ssr, getClaims) + dev adapter; proxy.ts refresh.
- /account (prefs, export, delete), saved places CRUD.

## Milestone C — map, search, routing, drive 🟡
- MapLibre GL 6 (lazy, worker served from /public), MapTiler/OpenFreeMap/fixture styles,
  3D building extrusion from `render_height`/`height` with 8 m fallback, 2D/3D, dark/light.
- Geoapify autocomplete/reverse/routing adapters behind provider interfaces; fixtures in demo.
- Navigator: debounced/cancelled search, recents, place sheet, route preview, guidance with GPS
  smoothing, stale detection, off-route reroute, arrival, voice (opt-in), WebGL fallback.
- /drive lean landscape UI; /app/map in shell.
- 🟡 Needs real MapTiler + Geoapify keys to verify Tbilisi 3D coverage and Georgian search quality.

## Milestone D — pairing + Send to Car ✅
- One-time QR (sha256-hashed token, 5 min TTL, single approval, nonce-bound redemption),
  device credential (hashed secret) cookie, rename/revoke, presence.
- Inbox: send → Realtime (Supabase) + 10 s polling + replay on reconnect/visibility; ack states.

## Milestone E — weather, FX, chargers 🟡
- Open-Meteo (commercial key; free tier only for non-commercial dev), NBG (JSON gateway + asmx
  fallback), Open Charge Map — tile-keyed TTL caches, stale-but-labelled fallback, attribution,
  usage metering + quota alerts. 🟡 Live endpoints unverified from this environment (blocked hosts).

## Milestone F — billing 🟡
- Paddle: server-created transaction with signed custom_data, overlay checkout, verified webhooks
  (raw body, multi-h1, 5 s tolerance), dedupe, out-of-order protection, scheduled-cancel modelling,
  past-due grace, portal sessions, cancel/undo via API, hourly reconciliation cron, one trial per
  email hash. Dev simulator drives the same pipeline. 🟡 Needs Paddle sandbox credentials.

## Milestone G — polish, tests, deploy ✅ (device testing ⛔)
- Vitest: entitlement, signature (cross-checked vs official SDK), nav math, provider parsers,
  RLS, webhook lifecycle, pairing/inbox. Playwright: vertical slice, pairing/replay/revoke,
  security, responsive (desktop / 1280×800 car / phone), no-GPS behaviour.
- ⛔ Real Tesla MCU2/MCU3 browser testing — requires a vehicle (docs/tesla-browser-testing.md).
