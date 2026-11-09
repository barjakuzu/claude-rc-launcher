# Deployment (Vercel + Supabase)

## 1. Supabase
1. Create a project. The EU region is closest to Georgia.
2. Apply the migrations, either with `supabase link --project-ref <ref> && supabase db push`, or by pasting `supabase/migrations/*.sql` into the SQL editor in order.
3. Auth → URL configuration: set the site URL to `https://<domain>` and add the redirect URL `https://<domain>/auth/callback`.
4. Auth → Email: configure custom SMTP (your transactional provider) and email templates.
5. Optional: Auth → Providers → Google, then set `NEXT_PUBLIC_GOOGLE_AUTH_ENABLED=true`.
6. Database → Replication: confirm `inbox_destinations` is in the `supabase_realtime` publication. The migration adds it when the publication exists.
7. Copy these values:
   - `NEXT_PUBLIC_SUPABASE_URL`
   - `NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY`
   - `SUPABASE_SECRET_KEY` (server only)
   - `DATABASE_URL`: the transaction pooler URI. The connecting role must be able to `SET ROLE authenticated`; the default `postgres` role can.

## 2. Vercel
1. Import the repo and set **Root Directory = `mapio`**.
2. Add every variable from `.env.example`. Do **not** set `MAPIO_DEMO_MODE` in production.
3. Set `SESSION_SECRET` and `CRON_SECRET` (random), and `APP_URL=https://<domain>`.
4. `vercel.json` already schedules `/api/cron/reconcile` hourly. Vercel sends `Authorization: Bearer $CRON_SECRET`.
5. Add the domain, then update the MapTiler key's allowed origins, Paddle approved domains, and the Supabase URLs.

## 3. Smoke test after deploy
- `/api/health` returns `{ok:true}`.
- `/admin`: sign in with an `ADMIN_EMAILS` account. Integrations should all show `live`; then run the provider health checks.
- Complete a sandbox trial end to end, then cancel and undo.
- Pair a second browser and send a destination.

## Credentials and approvals needed from you
- [ ] MapTiler commercial plan + API key, restricted to production/preview origins with quotas
- [ ] Geoapify API key; plan and quotas confirmed; commercial and caching terms reviewed
- [ ] Supabase project URL, publishable key, secret key, DB pooler URI; Google OAuth client (optional)
- [ ] Paddle seller approval; sandbox + live API keys, client tokens, webhook secrets; product with the 7-day-trial price and the no-trial price; approved domains
- [ ] Open-Meteo commercial API key
- [ ] Open Charge Map API key + licence/attribution review
- [ ] Confirmation of the official NBG data-access method (or a licensed FX alternative)
- [ ] Domain/DNS, Vercel project, transactional email provider (SPF/DKIM/DMARC)
- [ ] Counsel-approved Terms, Privacy Policy, refund policy; support email; legal entity details
- [ ] Real-vehicle browser test session (docs/tesla-browser-testing.md)
