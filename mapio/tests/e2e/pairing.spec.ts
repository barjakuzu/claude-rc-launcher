import { expect, test } from "@playwright/test";
import { fillLogin, signUp, startTrial, uniqueEmail } from "./helpers";

/** Car browser (landscape) pairs via one-time code approved on the phone, then receives a destination. */
test("QR pairing + send to car with reconnect replay", async ({ browser }) => {
  const email = uniqueEmail("pair");
  const car = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  const phone = await browser.newContext({ viewport: { width: 412, height: 915 }, isMobile: true, hasTouch: true, userAgent: "Mozilla/5.0 (Linux; Android 14) Mobile" });
  const carPage = await car.newPage();
  const phonePage = await phone.newPage();

  await signUp(carPage, email);
  await startTrial(carPage);

  // car: create pairing code
  await carPage.goto("/app/devices");
  await carPage.getByLabel("Device name").fill("Model 3 browser");
  await carPage.getByRole("button", { name: "Pair this browser" }).click();
  await expect(carPage.getByAltText(/Pairing QR code/)).toBeVisible();
  const approveUrl = (await carPage.locator("span.select-all").textContent())!.trim();
  expect(approveUrl).toMatch(/\/pair\/[A-Za-z0-9_-]+$/);

  // phone: open the QR link → login → approve
  await phonePage.goto(approveUrl);
  await expect(phonePage).toHaveURL(/\/login/);
  await fillLogin(phonePage, email);
  await expect(phonePage.getByRole("heading", { name: "Approve this browser?" })).toBeVisible();
  await expect(phonePage.getByText("Model 3 browser")).toBeVisible();
  await phonePage.getByRole("button", { name: "Approve pairing" }).click();
  await expect(phonePage.getByRole("heading", { name: "Browser paired" })).toBeVisible();

  // replay of the same QR is rejected
  await phonePage.goto(approveUrl);
  await expect(phonePage.getByText(/already been used/)).toBeVisible();

  // car: finishes pairing automatically
  await expect(carPage.getByText("This browser is paired")).toBeVisible({ timeout: 20_000 });

  // phone: send a destination to the car
  await phonePage.goto("/app/map");
  await phonePage.getByRole("textbox", { name: /Search destinations/ }).fill("Narikala");
  await phonePage.getByRole("option", { name: /Narikala Fortress/ }).click();
  await phonePage.getByRole("button", { name: "Send to Car" }).click();
  await expect(phonePage.getByText("Sent to your car browser")).toBeVisible();

  // car (was "offline" on another page): opens drive mode and gets the destination (replay on load)
  await carPage.goto("/drive");
  await carPage.getByRole("button", { name: /Sent to this browser/ }).click();
  const item = carPage.getByRole("region", { name: "Sent to this browser" }).getByText("Narikala Fortress");
  await expect(item).toBeVisible();

  // reload (reconnect) — still there until acted on
  await carPage.reload();
  await carPage.getByRole("button", { name: /Sent to this browser/ }).click();
  await expect(carPage.getByRole("region", { name: "Sent to this browser" }).getByText("Narikala Fortress")).toBeVisible();
  await carPage.getByRole("button", { name: "Navigate" }).first().click();
  await expect(carPage.getByRole("region", { name: "Selected place" })).toContainText("Narikala Fortress");

  // phone sees delivery state
  await phonePage.goto("/app/inbox");
  await expect(phonePage.getByText(/Navigating|Delivered|Opened/).first()).toBeVisible();

  // revoke from the phone → car is no longer paired
  await phonePage.goto("/app/devices");
  phonePage.once("dialog", (d) => d.accept());
  await phonePage.getByRole("button", { name: "Revoke" }).first().click();
  await expect(phonePage.getByText(/Revoked/)).toBeVisible();
  const inbox = await carPage.request.get("/api/inbox");
  expect((await inbox.json()).paired).toBe(false);

  await car.close();
  await phone.close();
});
