-- MAPIO initial schema.
-- Targets Supabase Postgres (auth.users, auth.uid(), roles anon/authenticated/service_role).
-- Local dev/tests run the same file on PGlite after src/lib/db/dev-shim.sql creates
-- minimal stand-ins for the Supabase auth schema and roles.
--
-- Security model:
--   * Every user-owned table has RLS enabled with owner-only policies.
--   * The server runs user-scoped queries as role `authenticated` with the user's
--     JWT subject set (see src/lib/db/index.ts withUser), so RLS applies to server
--     code too, not only to direct browser access (Realtime).
--   * Entitlement tables (subscriptions, processed_billing_events, trial_claims)
--     are readable by their owner at most and writable only by the service role.

create extension if not exists pgcrypto;

-- ── helpers ──────────────────────────────────────────────────────────────
create or replace function public.set_updated_at() returns trigger
language plpgsql as $$
begin
  new.updated_at = now();
  return new;
end $$;

-- ── profiles ─────────────────────────────────────────────────────────────
create table public.profiles (
  id uuid primary key references auth.users (id) on delete cascade,
  display_name text check (char_length(display_name) <= 80),
  locale text not null default 'en' check (locale in ('en', 'ka')),
  distance_unit text not null default 'km' check (distance_unit in ('km', 'mi')),
  temperature_unit text not null default 'c' check (temperature_unit in ('c', 'f')),
  time_format text not null default '24h' check (time_format in ('24h', '12h')),
  currency text not null default 'GEL' check (currency ~ '^[A-Z]{3}$'),
  timezone text not null default 'Asia/Tbilisi' check (char_length(timezone) <= 64),
  theme text not null default 'dark' check (theme in ('dark', 'light', 'system')),
  notify_inbox boolean not null default true,
  keep_recent_destinations boolean not null default true,
  role text not null default 'user' check (role in ('user', 'admin')),
  onboarded_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create trigger profiles_updated_at before update on public.profiles
  for each row execute function public.set_updated_at();

alter table public.profiles enable row level security;
create policy "profiles: owner read" on public.profiles
  for select to authenticated using (id = auth.uid());
create policy "profiles: owner insert" on public.profiles
  for insert to authenticated with check (id = auth.uid() and role = 'user');
create policy "profiles: owner update" on public.profiles
  for update to authenticated using (id = auth.uid()) with check (id = auth.uid());
-- Users may update preferences but never their own role.
revoke update on public.profiles from authenticated;
grant select, insert on public.profiles to authenticated;
grant update (display_name, locale, distance_unit, temperature_unit, time_format, currency,
              timezone, theme, notify_inbox, keep_recent_destinations, onboarded_at)
  on public.profiles to authenticated;

-- Create a profile row automatically for every new auth user.
create or replace function public.handle_new_user() returns trigger
language plpgsql security definer set search_path = public as $$
begin
  insert into public.profiles (id) values (new.id) on conflict (id) do nothing;
  return new;
end $$;
create trigger on_auth_user_created after insert on auth.users
  for each row execute function public.handle_new_user();

-- ── billing ──────────────────────────────────────────────────────────────
create table public.subscriptions (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users (id) on delete cascade,
  provider text not null check (provider in ('paddle', 'dev')),
  provider_customer_id text,
  provider_subscription_id text not null,
  status text not null check (status in ('trialing', 'active', 'past_due', 'paused', 'canceled')),
  price_id text,
  product_id text,
  unit_price_amount bigint,            -- minor units, as reported by the provider
  currency_code text,
  billing_interval text,               -- e.g. "1 month"
  trial_start timestamptz,
  trial_end timestamptz,
  current_period_start timestamptz,
  current_period_end timestamptz,
  next_billed_at timestamptz,
  scheduled_change_action text check (scheduled_change_action in ('cancel', 'pause', 'resume')),
  scheduled_change_effective_at timestamptz,
  canceled_at timestamptz,
  paused_at timestamptz,
  last_event_at timestamptz,           -- provider occurred_at of the newest applied event
  last_event_id text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (provider, provider_subscription_id)
);
create index subscriptions_user_idx on public.subscriptions (user_id);
create trigger subscriptions_updated_at before update on public.subscriptions
  for each row execute function public.set_updated_at();
alter table public.subscriptions enable row level security;
create policy "subscriptions: owner read" on public.subscriptions
  for select to authenticated using (user_id = auth.uid());
grant select on public.subscriptions to authenticated;
-- no insert/update/delete grants: only the service role writes entitlements.

create table public.processed_billing_events (
  provider_event_id text primary key,
  provider text not null,
  event_type text not null,
  occurred_at timestamptz not null,
  processed_at timestamptz not null default now(),
  provider_subscription_id text,
  outcome text not null check (outcome in ('applied', 'stale', 'ignored', 'error'))
);
alter table public.processed_billing_events enable row level security;
-- no policies: service role only.

-- One introductory trial per email (hash survives account deletion on purpose,
-- documented in the privacy policy as an anti-abuse record).
create table public.trial_claims (
  email_hash text primary key,
  user_id uuid references auth.users (id) on delete set null,
  claimed_at timestamptz not null default now()
);
alter table public.trial_claims enable row level security;

-- ── devices & pairing ────────────────────────────────────────────────────
create table public.paired_devices (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users (id) on delete cascade,
  name text not null check (char_length(name) between 1 and 60),
  device_type text not null default 'car_browser'
    check (device_type in ('car_browser', 'phone', 'desktop', 'other')),
  secret_hash text not null,           -- sha256 of the device credential; never the secret
  user_agent text check (char_length(user_agent) <= 300),
  created_at timestamptz not null default now(),
  last_seen_at timestamptz,
  revoked_at timestamptz
);
create index paired_devices_user_idx on public.paired_devices (user_id);
alter table public.paired_devices enable row level security;
create policy "devices: owner read" on public.paired_devices
  for select to authenticated using (user_id = auth.uid());
create policy "devices: owner update" on public.paired_devices
  for update to authenticated using (user_id = auth.uid()) with check (user_id = auth.uid());
create policy "devices: owner insert" on public.paired_devices
  for insert to authenticated with check (user_id = auth.uid());
grant select, insert on public.paired_devices to authenticated;
grant update (name, last_seen_at, revoked_at) on public.paired_devices to authenticated;

create table public.device_pairing_requests (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users (id) on delete cascade,
  token_hash text not null unique,     -- sha256 of the one-time QR token
  browser_nonce_hash text not null,    -- sha256 of an httpOnly nonce held by the requesting browser
  device_name text not null check (char_length(device_name) between 1 and 60),
  user_agent text check (char_length(user_agent) <= 300),
  expires_at timestamptz not null,
  approved_at timestamptz,
  approved_by uuid references auth.users (id) on delete set null,
  redeemed_at timestamptz,
  paired_device_id uuid references public.paired_devices (id) on delete set null,
  created_at timestamptz not null default now()
);
create index pairing_user_idx on public.device_pairing_requests (user_id);
alter table public.device_pairing_requests enable row level security;
create policy "pairing: owner all" on public.device_pairing_requests
  for all to authenticated using (user_id = auth.uid()) with check (user_id = auth.uid());
grant select, insert, update, delete on public.device_pairing_requests to authenticated;

-- ── places ───────────────────────────────────────────────────────────────
create table public.saved_places (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users (id) on delete cascade,
  kind text not null default 'favorite' check (kind in ('home', 'work', 'favorite')),
  label text not null check (char_length(label) between 1 and 60),
  name text not null check (char_length(name) between 1 and 200),
  address text check (char_length(address) <= 300),
  lat double precision not null check (lat between -90 and 90),
  lon double precision not null check (lon between -180 and 180),
  source text check (char_length(source) <= 40),
  source_ref text check (char_length(source_ref) <= 200),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create unique index saved_places_one_home_work on public.saved_places (user_id, kind)
  where kind in ('home', 'work');
create trigger saved_places_updated_at before update on public.saved_places
  for each row execute function public.set_updated_at();
alter table public.saved_places enable row level security;
create policy "saved: owner all" on public.saved_places
  for all to authenticated using (user_id = auth.uid()) with check (user_id = auth.uid());
grant select, insert, update, delete on public.saved_places to authenticated;

create table public.recent_destinations (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users (id) on delete cascade,
  name text not null check (char_length(name) between 1 and 200),
  address text check (char_length(address) <= 300),
  lat double precision not null check (lat between -90 and 90),
  lon double precision not null check (lon between -180 and 180),
  source text,
  used_at timestamptz not null default now()
);
create index recent_user_idx on public.recent_destinations (user_id, used_at desc);
alter table public.recent_destinations enable row level security;
create policy "recent: owner all" on public.recent_destinations
  for all to authenticated using (user_id = auth.uid()) with check (user_id = auth.uid());
grant select, insert, update, delete on public.recent_destinations to authenticated;

-- ── send-to-car inbox ────────────────────────────────────────────────────
create table public.inbox_destinations (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users (id) on delete cascade,
  target_device_id uuid not null references public.paired_devices (id) on delete cascade,
  name text not null check (char_length(name) between 1 and 200),
  address text check (char_length(address) <= 300),
  lat double precision not null check (lat between -90 and 90),
  lon double precision not null check (lon between -180 and 180),
  note text check (char_length(note) <= 200),
  sender_label text check (char_length(sender_label) <= 60),
  state text not null default 'sent'
    check (state in ('sent', 'received', 'opened', 'navigating', 'dismissed')),
  sent_at timestamptz not null default now(),
  received_at timestamptz,
  acted_at timestamptz
);
create index inbox_target_idx on public.inbox_destinations (target_device_id, sent_at desc);
create index inbox_user_idx on public.inbox_destinations (user_id, sent_at desc);
alter table public.inbox_destinations enable row level security;
create policy "inbox: owner read" on public.inbox_destinations
  for select to authenticated using (user_id = auth.uid());
create policy "inbox: owner insert to own device" on public.inbox_destinations
  for insert to authenticated with check (
    user_id = auth.uid()
    and exists (select 1 from public.paired_devices d
                where d.id = target_device_id and d.user_id = auth.uid() and d.revoked_at is null)
  );
create policy "inbox: owner update" on public.inbox_destinations
  for update to authenticated using (user_id = auth.uid()) with check (user_id = auth.uid());
create policy "inbox: owner delete" on public.inbox_destinations
  for delete to authenticated using (user_id = auth.uid());
grant select, insert, delete on public.inbox_destinations to authenticated;
grant update (state, received_at, acted_at) on public.inbox_destinations to authenticated;

-- ── usage, audit, flags ──────────────────────────────────────────────────
create table public.api_usage_daily (
  day date not null,
  provider text not null,
  user_key text not null,              -- user uuid or 'anon'
  requests integer not null default 0,
  cache_hits integer not null default 0,
  errors integer not null default 0,
  approx_cost_micros bigint not null default 0,
  primary key (day, provider, user_key)
);
alter table public.api_usage_daily enable row level security;

create table public.admin_audit_log (
  id uuid primary key default gen_random_uuid(),
  actor_id uuid references auth.users (id) on delete set null,
  action text not null,
  target_user_id uuid references auth.users (id) on delete set null,
  details jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);
alter table public.admin_audit_log enable row level security;

create table public.feature_flags (
  key text primary key check (key ~ '^[a-z0-9_]{2,40}$'),
  enabled boolean not null default false,
  description text,
  updated_at timestamptz not null default now(),
  updated_by uuid references auth.users (id) on delete set null
);
alter table public.feature_flags enable row level security;
create policy "flags: everyone read" on public.feature_flags
  for select to authenticated using (true);
grant select on public.feature_flags to authenticated;

-- Admin-granted access (audited). Kept apart from provider subscriptions so
-- provider webhooks can never overwrite a manual grant and vice versa.
create table public.access_grants (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users (id) on delete cascade,
  granted_by uuid references auth.users (id) on delete set null,
  reason text not null check (char_length(reason) between 3 and 300),
  expires_at timestamptz not null,
  revoked_at timestamptz,
  created_at timestamptz not null default now()
);
alter table public.access_grants enable row level security;
create policy "grants: owner read" on public.access_grants
  for select to authenticated using (user_id = auth.uid());
grant select on public.access_grants to authenticated;

insert into public.feature_flags (key, enabled, description) values
  ('chargers', true, 'Charger discovery layer and /app/chargers'),
  ('weather', true, 'Weather chip and forecast'),
  ('fx_rates', true, 'Official NBG reference exchange rates'),
  ('send_to_car', true, 'Phone-to-browser destination inbox')
on conflict do nothing;

-- ── Realtime (Supabase only) ─────────────────────────────────────────────
do $$
begin
  if exists (select 1 from pg_publication where pubname = 'supabase_realtime') then
    execute 'alter publication supabase_realtime add table public.inbox_destinations';
    execute 'alter publication supabase_realtime add table public.device_pairing_requests';
  end if;
end $$;
