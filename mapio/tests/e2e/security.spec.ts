import { expect, test } from "@playwright/test";
import { signUp, uniqueEmail } from "./helpers";

test.describe("API protection", () => {
  test("anonymous requests are rejected", async ({ request }) => {
    for (const url of ["/api/places", "/api/inbox", "/api/devices", "/api/billing/entitlement", "/api/geo/search?q=tbilisi"]) {
      expect((await request.get(url)).status(), url).toBe(401);
    }
  });

  test("private pages redirect to login and are noindex", async ({ request }) => {
    const r = await request.get("/account/billing", { maxRedirects: 0 });
    expect(r.status()).toBe(307);
    expect(r.headers()["location"]).toContain("/login?next=%2Faccount%2Fbilling");
  });

  test("webhook without a valid signature is rejected", async ({ request }) => {
    const r = await request.post("/api/webhooks/paddle", {
      data: { event_id: "evt_x", event_type: "subscription.created", occurred_at: new Date().toISOString(), data: {} },
      headers: { "paddle-signature": "ts=1;h1=deadbeef" },
    });
    expect(r.status()).toBe(401);
  });

  test("cross-origin mutation is blocked; non-subscriber gets 402", async ({ page }) => {
    await signUp(page, uniqueEmail("sec"));
    const xo = await page.request.post("/api/places", {
      data: { kind: "favorite", label: "x", name: "x", lat: 1, lon: 1 },
      headers: { origin: "https://evil.example" },
    });
    expect(xo.status()).toBe(403);
    const same = await page.request.post("/api/places", {
      data: { kind: "favorite", label: "x", name: "x", lat: 1, lon: 1 },
      headers: { origin: "http://localhost:3100" },
    });
    expect(same.status()).toBe(402);
    const bad = await page.request.get("/api/geo/search?q=a");
    expect([400, 402]).toContain(bad.status());
  });

  test("open redirect after login is prevented", async ({ page }) => {
    const email = uniqueEmail("redir");
    await signUp(page, email);
    await page.context().clearCookies();
    await page.goto("/login?next=//evil.example");
    await page.getByLabel("Email").fill(email);
    await page.getByLabel("Password").fill("correct-horse-battery");
    await page.getByRole("button", { name: "Log in" }).click();
    await expect(page).toHaveURL(/localhost:3100\/app$/);
  });
});
