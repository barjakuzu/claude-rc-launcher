// Captures the main MAPIO screens at the QA viewports and reports horizontal overflow.
// Usage: app running in demo mode on :3100 →  node scripts/visual-snapshots.mjs <outDir>
import { chromium } from "@playwright/test";
import fs from "node:fs";

const B = process.env.BASE_URL ?? "http://localhost:3100";
const OUT = process.argv[2] ?? "visual-snapshots";
fs.mkdirSync(OUT, { recursive: true });
const VIEWPORTS = [
  { name: "desktop-1440", width: 1440, height: 900 },
  { name: "laptop-1280", width: 1280, height: 800 },
  { name: "tesla-1920", width: 1920, height: 1200 },
  { name: "mobile-390", width: 390, height: 844, mobile: true },
  { name: "mobile-375", width: 375, height: 667, mobile: true },
];
const browser = await chromium.launch({ executablePath: process.env.PW_CHROMIUM_PATH || undefined });
const report = [];

// one account with an active trial, reused for every viewport
const setup = await browser.newContext();
const sp = await setup.newPage();
const email = `visual-${Date.now()}@mapio.test`;
await sp.goto(`${B}/signup`);
await sp.getByLabel("Email").fill(email);
await sp.getByLabel("Password").fill("visual-password-1");
await sp.getByRole("checkbox").check();
await sp.getByRole("button", { name: "Create account" }).click();
await sp.waitForURL(/onboarding/);
await sp.getByLabel("Display name").fill("Tamar").catch(() => {});
await sp.getByRole("button", { name: "Continue" }).click();
await sp.waitForURL(/\/app$/);
await sp.request.post(`${B}/api/dev/billing`, { data: { action: "start_trial" }, headers: { origin: B } });
await sp.request.post(`${B}/api/places`, { data: { kind: "home", label: "Home", name: "Vake Park", address: "Vake, Tbilisi", lat: 41.7069, lon: 44.752 }, headers: { origin: B } });
await sp.request.post(`${B}/api/places`, { data: { kind: "favorite", label: "Narikala Fortress", name: "Narikala Fortress", address: "Old Tbilisi", lat: 41.688, lon: 44.8086 }, headers: { origin: B } });
await sp.request.post(`${B}/api/places/recent`, { data: { name: "Holy Trinity Cathedral", address: "Avlabari, Tbilisi", lat: 41.6974, lon: 44.8166 }, headers: { origin: B } });
const state = await setup.storageState();
await setup.close();

for (const vp of VIEWPORTS) {
  const ctx = await browser.newContext({
    viewport: { width: vp.width, height: vp.height },
    deviceScaleFactor: 1,
    isMobile: !!vp.mobile,
    hasTouch: !!vp.mobile,
    storageState: state,
  });
  await ctx.grantPermissions(["geolocation"], { origin: B });
  await ctx.setGeolocation({ latitude: 41.6934, longitude: 44.8015 });
  const p = await ctx.newPage();
  const pages = [
    ["landing", "/", null],
    ["dashboard", "/app", null],
    ["map", "/app/map", async () => {
      await p.getByRole("textbox", { name: /Search destinations/ }).fill("Narikala");
      await p.getByRole("option", { name: /Narikala/ }).click();
      await p.getByRole("button", { name: "Directions" }).click();
    }],
    ["drive", "/drive", null],
    ["billing", "/account/billing", null],
    ["devices", "/app/devices", null],
    ["inbox", "/app/inbox", null],
  ];
  for (const [name, path, act] of pages) {
    if (name === "landing") await ctx.clearCookies();
    await p.goto(B + path);
    if (name === "landing") await ctx.addCookies(state.cookies);
    await p.waitForTimeout(1800);
    if (act) {
      try { await act(); await p.waitForTimeout(1800); } catch (e) { report.push(`${vp.name}/${name}: action failed ${e.message.split("\n")[0]}`); }
    }
    // Page scroll overflow, plus clipped overflow: any visible element extending past the viewport.
    const { overflow, culprit } = await p.evaluate(() => {
      const vw = Math.min(window.innerWidth, document.documentElement.clientWidth, screen.width || Infinity);
      let worst = 0, culprit = "";
      for (const el of document.querySelectorAll("body *")) {
        const r = el.getBoundingClientRect();
        if (!r.width || !r.height) continue;
        const cs = getComputedStyle(el);
        if (cs.position === "fixed" || cs.visibility === "hidden") continue;
        if (el.closest(".maplibregl-map, [data-allow-overflow], .overflow-x-auto")) continue;
        const over = r.right - vw;
        if (over > worst) { worst = over; culprit = `${el.tagName.toLowerCase()}.${String(el.className).split(" ").slice(0, 3).join(".")}`; }
      }
      return { overflow: Math.max(worst, document.documentElement.scrollWidth - vw, (window.visualViewport?.width ?? vw) < window.innerWidth ? window.innerWidth - vw : 0), culprit };
    });
    if (overflow > 1) report.push(`${vp.name}/${name}: horizontal overflow ${Math.round(overflow)}px (${culprit})`);
    if (vp.mobile) {
      const small = await p.evaluate(() =>
        [...document.querySelectorAll("button, [role=button], nav a, a.btn")]
          .filter((el) => {
            const r = el.getBoundingClientRect();
            return r.width > 0 && r.height > 0 && (r.height < 40 || r.width < 40) && !el.closest("footer, .maplibregl-ctrl, p, .sr-only") && !el.classList.contains("sr-only");
          })
          .map((el) => (el.getAttribute("aria-label") || el.textContent || "").trim().slice(0, 30)),
      );
      if (small.length) report.push(`${vp.name}/${name}: ${small.length} touch target(s) under 40px: ${small.slice(0, 5).join(" | ")}`);
    }
    await p.screenshot({ path: `${OUT}/${vp.name}-${name}.png` });
  }
  await ctx.close();
}
await browser.close();
console.log(report.length ? report.join("\n") : "no overflow detected");
