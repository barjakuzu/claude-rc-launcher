import { describe, expect, it } from "vitest";
import { Paddle, Environment } from "@paddle/paddle-node-sdk";
import { computeSignature, parseSignatureHeader, signatureHeader, verifySignature } from "@/lib/billing/signature";

const secret = "pdl_ntfset_test_secret";
const body = JSON.stringify({ event_id: "evt_1", event_type: "subscription.created", occurred_at: "2026-11-09T00:00:00Z", data: {} });

describe("Paddle webhook signature", () => {
  it("accepts a valid signature", () => {
    expect(verifySignature(secret, body, signatureHeader(secret, body))).toBe(true);
  });

  it("rejects tampered body, wrong secret, missing header, garbage", () => {
    const h = signatureHeader(secret, body);
    expect(verifySignature(secret, body + " ", h)).toBe(false);
    expect(verifySignature("other", body, h)).toBe(false);
    expect(verifySignature(secret, body, null)).toBe(false);
    expect(verifySignature(secret, body, "nonsense")).toBe(false);
  });

  it("rejects replays outside the tolerance window", () => {
    const ts = Math.floor(Date.now() / 1000) - 60;
    expect(verifySignature(secret, body, `ts=${ts};h1=${computeSignature(secret, ts, body)}`)).toBe(false);
  });

  it("accepts any matching h1 during secret rotation", () => {
    const ts = Math.floor(Date.now() / 1000);
    const header = `ts=${ts};h1=${computeSignature("old", ts, body)};h1=${computeSignature(secret, ts, body)}`;
    expect(parseSignatureHeader(header)?.h1).toHaveLength(2);
    expect(verifySignature(secret, body, header)).toBe(true);
  });

  it("agrees with the official Paddle Node SDK validator", async () => {
    const paddle = new Paddle("pdl_sdbx_apikey_dummy", { environment: Environment.sandbox });
    const header = signatureHeader(secret, body);
    expect(await paddle.webhooks.isSignatureValid(body, secret, header)).toBe(true);
    expect(await paddle.webhooks.isSignatureValid(body + "x", secret, header)).toBe(false);
  });
});
