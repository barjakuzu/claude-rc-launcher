import { expect, test } from "@playwright/test";
import { signUp, startTrial, uniqueEmail } from "./helpers";

test("landing has no horizontal overflow", async ({ page }) => {
  await page.goto("/");
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow).toBeLessThanOrEqual(1);
});

test("drive mode renders a map-first layout with one toolbar", async ({ page }) => {
  await signUp(page, uniqueEmail("drive"));
  await startTrial(page);
  await page.goto("/drive");
  await expect(page.getByRole("region", { name: "Navigation map" })).toBeVisible();
  await expect(page.getByRole("toolbar", { name: "Map controls" })).toBeVisible();
  await expect(page.getByRole("textbox", { name: /Search destinations/ })).toBeVisible();
  // no account/billing panels in drive mode
  await expect(page.getByText("MAPIO Premium")).toHaveCount(0);
  // map canvas actually rendered (WebGL)
  await expect(page.locator("canvas.maplibregl-canvas")).toBeVisible();
  // 2D/3D toggle works
  await page.getByRole("button", { name: /^(2D|3D)$/ }).click();
});
