// Captures public/preview/map-3d.png from MAPIO's own /drive view (demo fixture map + demo route).
// Usage: start the app in demo mode on :3100 (see .env.e2e), then `npm run preview:capture`.
import { chromium } from "@playwright/test";

const BASE = process.env.BASE_URL ?? "http://localhost:3100";
const browser = await chromium.launch({ executablePath: process.env.PW_CHROMIUM_PATH || undefined });
const ctx = await browser.newContext({ viewport: { width: 1600, height: 1000 }, deviceScaleFactor: 1 });
await ctx.grantPermissions(["geolocation"], { origin: BASE });
// Origin and destination sit on intersections of the synthetic fixture grid so the demo route follows streets.
await ctx.setGeolocation({ latitude: 41.6934, longitude: 44.8015 });
await ctx.addInitScript((theme) => { try { localStorage.setItem("mapio_theme", theme); } catch {} }, process.env.PREVIEW_THEME ?? "light");
const page = await ctx.newPage();
const email = `preview-${Date.now()}@mapio.test`;
await page.goto(`${BASE}/signup`);
await page.getByLabel("Email").fill(email);
await page.getByLabel("Password").fill("preview-password-123");
await page.getByRole("checkbox").check();
await page.getByRole("button", { name: "Create account" }).click();
await page.waitForURL(/onboarding/);
await page.getByRole("button", { name: "Continue" }).click();
await page.waitForURL(/\/app$/);
await page.request.post(`${BASE}/api/dev/billing`, { data: { action: "start_trial" }, headers: { origin: BASE } });
await page.goto(`${BASE}/drive?lat=${41.6934 + 3 * 0.0022}&lon=${44.8015 + 4 * 0.0022}&name=Preview`);
await page.getByRole("button", { name: "Directions" }).click();
await page.waitForTimeout(3500);
// hide UI chrome so only the map render remains
await page.addStyleTag({ content: "main > div > :not(.absolute.inset-0){display:none!important} .maplibregl-ctrl-bottom-right{display:none!important}" });
await page.waitForTimeout(500);
await page.screenshot({ path: "public/preview/map-3d.png" });
await browser.close();
console.log("wrote public/preview/map-3d.png");
