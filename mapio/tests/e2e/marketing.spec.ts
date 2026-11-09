import { expect, test } from "@playwright/test";

test.describe("public marketing pages", () => {
  test("landing: hero, trust bar, features, pricing and disclaimers", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByRole("heading", { level: 1 })).toContainText("3D navigation for your");
    await expect(page.getByRole("link", { name: /Start 7-day free trial/ }).first()).toBeVisible();
    await expect(page.getByText("Cancel anytime").first()).toBeVisible();
    for (const f of ["3D Navigation", "Send to Car", "Charging Stations", "Weather & Currency", "Saved Places", "Subscription Management"]) {
      await expect(page.getByRole("heading", { name: f, exact: true })).toBeVisible();
    }
    await expect(page.getByRole("heading", { name: "MAPIO Premium" })).toBeVisible();
    await expect(page.getByText("not affiliated with Tesla").first()).toBeVisible();
    // no fabricated live data on the marketing visual
    await expect(page.getByText("availability unknown")).toBeVisible();
  });

  test("pricing explains trial, renewal and cancellation", async ({ page }) => {
    await page.goto("/pricing");
    await expect(page.getByRole("heading", { name: "How the trial works" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Renewal" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Cancel anytime" })).toBeVisible();
    await expect(page.getByText(/yearly/i)).toBeVisible(); // "No yearly plan"
  });

  test("legal pages are clearly marked as drafts", async ({ page }) => {
    for (const p of ["/terms", "/privacy"]) {
      await page.goto(p);
      await expect(page.getByText(/requires review by qualified legal counsel/)).toBeVisible();
    }
  });

  test("features page separates available / browser-dependent / unsupported", async ({ page }) => {
    await page.goto("/features");
    await expect(page.getByRole("heading", { name: "Not supported" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Live traffic" })).toBeVisible();
  });

  test("robots and sitemap", async ({ request }) => {
    const robots = await (await request.get("/robots.txt")).text();
    expect(robots).toContain("Disallow: /app");
    expect(robots).toContain("Disallow: /account");
    const sitemap = await (await request.get("/sitemap.xml")).text();
    expect(sitemap).toContain("/pricing");
    expect(sitemap).not.toContain("/app");
  });
});
