import { expect, test } from "@playwright/test";
import { signUp, startTrial, uniqueEmail } from "./helpers";

/**
 * landing → sign up → paywall → trial checkout (dev simulator, signed webhook) →
 * entitlement → search → route → save → manage subscription.
 * Runs with demo adapters (fixture map/search/routing) — no real provider calls.
 */
test.describe.configure({ mode: "serial" });

test("full vertical slice", async ({ page, context }) => {
  await context.grantPermissions(["geolocation"]);
  await context.setGeolocation({ latitude: 41.6934, longitude: 44.8015 }); // Freedom Square

  await page.goto("/");
  await page.getByRole("link", { name: /Start 7-day free trial/ }).first().click();
  await expect(page).toHaveURL(/\/signup/);

  const email = uniqueEmail("slice");
  await signUp(page, email);
  await expect(page.getByRole("heading", { name: /Hello/ })).toBeVisible();

  // private feature is gated until a verified webhook grants entitlement
  await page.goto("/app/map");
  await expect(page.getByRole("heading", { name: /The map is part of MAPIO Premium/ })).toBeVisible();
  const before = await page.request.get("/api/geo/search?q=freedom&lang=en");
  expect(before.status()).toBe(402);

  await startTrial(page);
  await expect(page.getByText("Trial ends").first()).toBeVisible();
  await expect(page.getByText(/Development simulator/)).toBeVisible();

  // search → place → route
  await page.goto("/app/map");
  const search = page.getByRole("textbox", { name: /Search destinations/ });
  await search.fill("Holy Trinity");
  await page.getByRole("option", { name: /Holy Trinity Cathedral/ }).click();
  await expect(page.getByRole("region", { name: "Selected place" })).toContainText("Holy Trinity Cathedral");

  await page.getByRole("button", { name: "Save" }).click();
  await expect(page.getByText("Saved to your places")).toBeVisible();

  await page.getByRole("button", { name: "Directions" }).click();
  const preview = page.getByRole("region", { name: "Route preview" });
  await expect(preview).toBeVisible();
  await expect(preview).toContainText("Demo route"); // honest demo labelling
  await expect(preview).toContainText("My location");

  // guidance with (emulated) GPS
  await page.getByRole("button", { name: "Start" }).click();
  await expect(page.getByRole("region", { name: "Trip progress" })).toBeVisible();
  await page.getByRole("button", { name: "End route" }).click();

  // saved + recent show up on the dashboard
  await page.goto("/app");
  await expect(page.getByText("Holy Trinity Cathedral").first()).toBeVisible();

  // manage subscription: schedule cancel during trial, then undo
  await page.goto("/account/billing");
  await page.getByRole("button", { name: "Cancel subscription" }).click();
  await page.getByRole("button", { name: "Yes, cancel at period end" }).click();
  await expect(page.getByText("Cancels at period end")).toBeVisible();
  await expect(page.getByText("Access until", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Keep my subscription" }).click();
  await expect(page.getByText("Free trial").first()).toBeVisible();

  // trial ends without payment → access removed (via simulated portal webhook)
  await page.goto("/dev/billing/portal");
  await page.getByRole("button", { name: /payment fails → canceled/ }).click();
  await expect(page.getByText(/Status now: canceled/)).toBeVisible();
  await page.goto("/app/map");
  await expect(page.getByRole("heading", { name: /part of MAPIO Premium/ })).toBeVisible();
  await expect(page.getByText(/Resubscribe/)).toBeVisible();
});

test("without GPS: route starts from the previous map view and guidance waits for a fix", async ({ page }) => {
  await (await import("./helpers")).signUp(page, uniqueEmail("nogps"));
  await startTrial(page);
  await page.goto("/drive");
  await page.getByRole("textbox", { name: /Search destinations/ }).fill("Holy Trinity");
  await page.getByRole("option", { name: /Holy Trinity Cathedral/ }).click();
  await page.getByRole("button", { name: "Directions" }).click();
  const preview = page.getByRole("region", { name: "Route preview" });
  await expect(preview).toContainText("Map center");
  await expect(preview).toContainText("km"); // a real distance, not a zero-length route
  await page.getByRole("button", { name: "Start" }).click();
  await expect(page.getByText("Waiting for GPS…")).toBeVisible();
});
