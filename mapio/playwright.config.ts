import { defineConfig, devices } from "@playwright/test";
import fs from "node:fs";

// Load the demo-only E2E environment (no real credentials).
const e2eEnv = Object.fromEntries(
  fs
    .readFileSync(".env.e2e", "utf8")
    .split("\n")
    .filter((l) => l && !l.startsWith("#"))
    .map((l) => [l.slice(0, l.indexOf("=")), l.slice(l.indexOf("=") + 1)]),
);

const executablePath = process.env.PW_CHROMIUM_PATH || undefined;
const PORT = 3100;

export default defineConfig({
  testDir: "tests/e2e",
  timeout: 60_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  reporter: [["list"]],
  use: {
    baseURL: `http://localhost:${PORT}`,
    trace: "off",
    launchOptions: { executablePath },
  },
  projects: [
    { name: "desktop", use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 }, launchOptions: { executablePath } } },
    { name: "car-1280x800", use: { viewport: { width: 1280, height: 800 }, launchOptions: { executablePath } }, testMatch: /responsive|drive/ },
    { name: "phone", use: { ...devices["Pixel 7"], launchOptions: { executablePath } }, testMatch: /responsive/ },
  ],
  webServer: {
    command: `rm -rf ${e2eEnv.MAPIO_DEV_DB_DIR} && npx next start -p ${PORT}`,
    url: `http://localhost:${PORT}/api/health`,
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
    env: { ...e2eEnv, NODE_ENV: "production" },
  },
});
