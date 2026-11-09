import { expect, type Page } from "@playwright/test";

export const uniqueEmail = (tag: string) => `${tag}-${Date.now()}-${Math.floor(Math.random() * 1e6)}@mapio.test`;
export const PASSWORD = "correct-horse-battery";

export async function signUp(page: Page, email: string) {
  await page.goto("/signup");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("checkbox").check();
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page).toHaveURL(/\/onboarding/);
  await page.getByRole("button", { name: "Continue" }).click();
  await expect(page).toHaveURL(/\/app$/);
}

/** Fills the login form on the CURRENT page (keeps any ?next= the app added). */
export async function fillLogin(page: Page, email: string) {
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Log in" }).click();
}

export async function logIn(page: Page, email: string, next?: string) {
  await page.goto(next ? `/login?next=${encodeURIComponent(next)}` : "/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Log in" }).click();
}

/** Starts the trial through the dev billing simulator (signed webhook pipeline). */
export async function startTrial(page: Page) {
  await page.goto("/account/billing");
  await page.getByRole("button", { name: /Start \d+-day free trial/ }).first().click();
  await expect(page).toHaveURL(/\/dev\/billing\/checkout/);
  await page.getByRole("button", { name: "Simulate successful checkout" }).click();
  await expect(page.getByText("Free trial").first()).toBeVisible();
}
