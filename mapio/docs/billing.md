# Billing (Paddle Billing)

References:
- Trials: https://developer.paddle.com/concepts/subscriptions/trials/
- Provisioning with webhooks: https://developer.paddle.com/build/subscriptions/provision-access-webhooks/
- Go-live checklist: https://developer.paddle.com/build/go-live-checklist/

## Flow
1. **Server creates a transaction.** `POST /api/billing/checkout` creates a Paddle transaction server-side:
   - `price_id = PADDLE_PRICE_ID` (monthly, 7-day trial), or `PADDLE_PRICE_ID_NO_TRIAL` if this email already used a trial.
   - `custom_data = { mapio_user_id, mapio_sig }`, an HMAC that stops forged ownership.
2. **Overlay checkout.** The browser opens the Paddle.js overlay with that `transactionId`. Paddle collects the payment method (the standard trial requires one).
3. **Redirect without access.** The success URL is `/account/billing?checkout=complete`. That page **does not grant access**; it polls `/api/billing/entitlement` until the webhook lands.
4. **Webhook.** `POST /api/webhooks/paddle`:
   - Verifies `Paddle-Signature` against the **raw body** (ts tolerance 5 s, several `h1` values accepted for secret rotation).
   - Dedupes on `event_id`, rejects stale (out-of-order) events using `occurred_at` versus `last_event_at`, and upserts `subscriptions`.
   - Records the trial claim (email hash).
5. **Entitlement.** `lib/billing/entitlement.ts` decides access (see docs/architecture.md).
6. **Manage.**
   - **Cancel:** `POST /api/billing/cancel` calls `POST /subscriptions/{id}/cancel {effective_from:"next_billing_period"}`. This is a scheduled cancel: access continues and the UI shows "Cancels at period end".
   - **Undo:** `POST /api/billing/undo-cancel` calls `PATCH /subscriptions/{id} {scheduled_change:null}`.
   - **Portal:** `POST /api/billing/portal` creates a customer portal session for payment method, invoices, and cancellation.
   - The API responses are written immediately through the same ordered `applyBillingEvent` path.
7. **Reconcile.** Vercel Cron runs `/api/cron/reconcile` hourly. It re-fetches non-canceled subscriptions (safety net for missed webhooks) and enforces inbox retention.

Handled events: `subscription.created | updated | activated | trialing | past_due | paused | resumed | canceled`. Other event types are recorded as `ignored`.

## Sandbox setup (you)
1. Create a Paddle **sandbox** account (sandbox-vendors.paddle.com).
2. Catalog → Products: create **MAPIO Premium**.
3. Create two prices on it:
   - **Monthly with trial:** price e.g. $6/month, billing every 1 month, trial period 7 days. Its id goes in `PADDLE_PRICE_ID`.
   - **Monthly without trial:** same price, no trial. Its id goes in `PADDLE_PRICE_ID_NO_TRIAL`.
4. Developer tools → Authentication: create an **API key** (`PADDLE_API_KEY`) and a **client-side token** (`NEXT_PUBLIC_PADDLE_CLIENT_TOKEN`).
5. Developer tools → Notifications: add a destination at `https://<your-domain>/api/webhooks/paddle`.
   - Subscribe to all `subscription.*` events.
   - Copy its secret into `PADDLE_WEBHOOK_SECRET`.
   - For local testing, expose your dev server with a tunnel (e.g. `cloudflared tunnel --url http://localhost:3000`) and use the generated URL.
6. Checkout settings: add your domain(s) to approved domains and set a default payment link (Paddle requires one).
7. Set `PADDLE_ENVIRONMENT=sandbox`, then test with Paddle's test cards:
   - trial start
   - simulated renewal (Paddle "simulations")
   - failed payment
   - cancel and undo
   - portal

## Production
- Paddle **seller verification** for the business entity must be approved first. MAPIO does not bypass it.
- Repeat the setup in the live account and set `PADDLE_ENVIRONMENT=production`.
- Verify domains and complete Paddle's go-live checklist.

## Business rules (configurable in code)
- **One trial per customer:** keyed on an email hash that survives account deletion (disclosed in the privacy draft).
- **past_due grace:** 7 days (`PAST_DUE_GRACE_DAYS`).
- **Admin access grants:** audited and time-limited. They are stored apart from provider subscriptions, so webhooks never overwrite them.
- **Prices are never hard-coded:** live prices come from `GET /prices/{id}`, cached for one hour. Configured `DISPLAY_PRICE_*` values are used only as a labelled fallback.

## Dev simulator
When `BILLING_PROVIDER=dev`, or Paddle isn't configured outside production, checkout and the portal go to `/dev/billing/*`.
- Each action builds a Paddle-shaped event, signs it with `DEV_BILLING_WEBHOOK_SECRET`, and runs it through the same `handleWebhook()` path.
- It is refused in production unless `MAPIO_DEMO_MODE=true`, and the pages say clearly that no payment is taken.
