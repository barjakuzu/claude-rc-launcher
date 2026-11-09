# Architecture

## Rendering model
Next.js 16 App Router. **`cacheComponents` is disabled on purpose.** Almost every page reads the session, so I chose classic dynamic rendering for authenticated routes. Auth and entitlement checks then stay in one obvious place. Marketing pages are static and revalidate hourly, because they read the provider price.

`proxy.ts` (formerly middleware) does three things:
- Refreshes Supabase cookies.
- Sends private routes to `/login` when there's no session. This is an optimistic check only; the real check is in each page/handler.
- Sets `X-Robots-Tag: noindex` and `Cache-Control: private, no-store` on private pages.

## Layers
```
UI (server components + client islands: Navigator, MapView, billing controls)
  └─ route handlers  src/app/api/*  — lib/api.ts: same-origin check, auth (401), entitlement (402),
                                       rate limit (429), zod validation (400), error mapping
       └─ services   lib/billing, lib/providers/*, lib/repos/*
            └─ data  lib/db: withUser(uid, fn)  → SET LOCAL ROLE authenticated + JWT claims (RLS enforced)
                             withService(fn)    → owner connection (webhooks, cron, pairing token lookup, admin aggregates)
```
- **Database driver:** `DATABASE_URL` uses the Supabase Postgres pooler through `pg`. Without it, dev and tests use **PGlite**, which runs `src/lib/db/dev-shim.sql` (stand-ins for the `auth` schema and roles) and then the real migrations.
- **RLS in tests:** the same SQL runs in both modes, so the integration tests exercise the production RLS policies.
- **Realtime:** the browser subscribes to `inbox_destinations` through the Supabase client using the publishable key, and RLS limits rows to the owner. Polling is the fallback.

## Security model
- **Owner-only RLS:** every user table has owner-only RLS policies. Column grants block role changes and entitlement writes.
- **Webhook-only entitlements:** subscriptions are written only by verified webhooks or reconciliation. A checkout redirect never grants access.
- **Webhook ownership:** checkout `custom_data` is HMAC-signed, so a forged user id in a webhook is ignored.
- **Pairing:** one-time token stored as sha256, 5-minute TTL, approval only by the same account, redemption bound to an httpOnly nonce held by the requesting browser. The device secret is stored as sha256 only.
- **CSRF:**
  - Cookie-authenticated mutations require a same-origin `Origin` header.
  - Session cookies are `SameSite=Lax`.
  - Server actions have Next's built-in origin checks.
- **Redirects and secrets:** post-login redirects are restricted by `safeNext()`. Secrets are server-only, and the only public keys are the MapTiler key (origin-restricted), the Supabase publishable key, and the Paddle client token.
- **Logging:** analytics and logs don't record precise location or tokens. Provider caches key on rounded grid tiles.

## Entitlements
`lib/billing/entitlement.ts` (pure, unit-tested):
- `trialing` and `active` grant access.
- A scheduled cancel keeps access until it takes effect and displays as `scheduled_cancel`.
- `past_due` keeps a 7-day grace period from the failed renewal.
- `paused` and `canceled` remove access.
- Audited admin `access_grants` grant access separately.

## Observability seams
- **Errors:** `lib/api.ts route()` is the single error funnel. Plug Sentry or another reporter in there.
- **Usage:** `lib/providers/usage.ts` writes daily per-provider counters to `api_usage_daily`. `/admin` shows them against quotas.
- **Analytics:** none is enabled. If you add it, choose privacy-friendly analytics, don't send coordinates, and update the privacy policy and consent.

## Performance
- **Lazy loading:** MapLibre loads lazily with `next/dynamic` (`ssr:false`). The dashboard mini-map loads on idle.
- **Low-performance mode:** when `prefers-reduced-motion`, `deviceMemory ≤ 2`, `hardwareConcurrency ≤ 2`, or `?perf=low` applies, `data-perf=low` is set. It turns off blur, lowers the pixel ratio, disables fades and antialiasing, defaults to 2D, and caps pitch at 50.
- **Throttling:** GPS updates are throttled to about 1 Hz, and `last_seen` writes to one per minute.
- **3D cost:** buildings start at zoom 14.
- **No WebGL:** a fallback message appears, and search and route steps still work.
