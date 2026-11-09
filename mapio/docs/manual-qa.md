# Manual QA

Run with live credentials (sandbox) before launch. The demo-mode equivalents are automated in `tests/e2e`.

## Accounts and access
- [ ] Sign up with email confirmation (Supabase). The confirmation link lands on `/onboarding`.
- [ ] Password reset email → `/reset-password` → can sign in with the new password.
- [ ] Google sign-in, if enabled.
- [ ] Signed-out access to `/app`, `/drive`, `/account`, `/admin` redirects to login with `next`.
- [ ] Non-admin gets 404 on `/admin`.

## Billing (Paddle sandbox)
- [ ] Trial checkout with a test card. Access appears only after the webhook (watch `/admin` webhook count).
- [ ] Billing page shows the trial end date, next charge date, and the amount from Paddle.
- [ ] Cancel during the trial shows "Cancels at period end". Undo restores it. The portal shows the same state.
- [ ] Simulate a renewal: subscription becomes `active`.
- [ ] Simulate a failed payment: `past_due` with the grace banner and "Update payment method".
- [ ] Same email signs up again after deletion: no second trial (no-trial price is used).
- [ ] Replaying a webhook from the Paddle dashboard is a no-op (`duplicate`).

## Map and navigation (real keys)
- [ ] MapTiler dark/light styles load; Tbilisi buildings extrude at zoom 15–17 (Rustaveli, Vake, Saburtalo).
- [ ] Georgian queries ("თავისუფლების მოედანი", "ვაკის პარკი") and English queries return sensible results.
- [ ] Route Freedom Square → Tbilisi Airport: the geometry follows roads and the steps are readable. The no-traffic notice is visible.
- [ ] Guidance on a phone while walking or as a car passenger: the turn card advances and off-route triggers a reroute.
- [ ] Chargers near Tbilisi: source, licence and "availability unknown" are shown; filters work.
- [ ] Weather shows its observation time. NBG rates show the publication date and the "reference rate" note.

## Send to Car
- [ ] Pair a laptop as the "car" from a phone: QR → approve → paired within seconds.
- [ ] Send from the phone while the car tab is closed, then open `/drive`: the item appears.
- [ ] Realtime: with the car tab open, the item appears in about a second (live Supabase), not after 10 s.
- [ ] Revoke the device: further sends fail and the car shows unpaired.

## Accessibility and responsive
- [ ] Keyboard-only: skip link, focus rings, all controls reachable. The screen reader announces turn cards (`aria-live`).
- [ ] 1280×800, 1920×1080, phone portrait: no overflow; the map fills at least 75% in `/drive`.
- [ ] `?perf=low` and reduced motion: no blur or animations.
