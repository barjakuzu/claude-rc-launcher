-- DEV FIXTURES ONLY (supabase db reset). Do not run against production.
-- Users are created through the app's sign-up flow; ADMIN_EMAILS promotes admins.
insert into public.feature_flags (key, enabled, description) values
  ('chargers', true, 'Charger discovery layer and /app/chargers'),
  ('weather', true, 'Weather chip and forecast'),
  ('fx_rates', true, 'Official NBG reference exchange rates'),
  ('send_to_car', true, 'Phone-to-browser destination inbox')
on conflict (key) do nothing;
