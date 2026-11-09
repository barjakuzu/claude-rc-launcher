import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { closeDb, withService } from "@/lib/db";
import {
  approvePairing,
  createPairingRequest,
  pairingStatus,
  redeemPairing,
  resolveDevice,
  revokeDevice,
} from "@/lib/repos/devices";
import { actOnInbox, fetchDeviceInbox, sendToDevice } from "@/lib/repos/inbox";
import { createUser } from "../support/db";

let owner: string;
let other: string;

beforeAll(async () => {
  owner = await createUser("pair-owner@example.test");
  other = await createUser("pair-other@example.test");
});
afterAll(closeDb);

describe("QR device pairing", () => {
  it("full flow: create → approve (phone) → redeem (car) → device credential", async () => {
    const req = await createPairingRequest(owner, "Model 3 browser", "Mozilla/5.0 (X11; Linux) Tesla");
    expect(await pairingStatus(owner, req.requestId)).toBe("pending");
    await expect(redeemPairing(owner, req.requestId, req.nonce)).rejects.toThrow(/not been approved/);

    await approvePairing(owner, req.token);
    expect(await pairingStatus(owner, req.requestId)).toBe("approved");

    const { credential, deviceId } = await redeemPairing(owner, req.requestId, req.nonce);
    expect(await pairingStatus(owner, req.requestId)).toBe("redeemed");
    const dev = await resolveDevice(owner, credential);
    expect(dev?.id).toBe(deviceId);
    expect(dev).not.toHaveProperty("secret_hash");
  });

  it("QR token cannot be replayed (approve twice / redeem twice)", async () => {
    const req = await createPairingRequest(owner, "Car", null);
    await approvePairing(owner, req.token);
    await expect(approvePairing(owner, req.token)).rejects.toThrow(/already been used/);
    await redeemPairing(owner, req.requestId, req.nonce);
    await expect(redeemPairing(owner, req.requestId, req.nonce)).rejects.toThrow(/already been used/);
  });

  it("another account cannot approve or redeem", async () => {
    const req = await createPairingRequest(owner, "Car", null);
    await expect(approvePairing(other, req.token)).rejects.toThrow(/different MAPIO account/);
    await approvePairing(owner, req.token);
    await expect(redeemPairing(other, req.requestId, req.nonce)).rejects.toThrow(/not found/);
  });

  it("must be redeemed by the browser holding the nonce", async () => {
    const req = await createPairingRequest(owner, "Car", null);
    await approvePairing(owner, req.token);
    await expect(redeemPairing(owner, req.requestId, "wrong-nonce")).rejects.toThrow(/browser that created/);
    await expect(redeemPairing(owner, req.requestId, undefined)).rejects.toThrow(/browser that created/);
  });

  it("expired codes are rejected", async () => {
    const req = await createPairingRequest(owner, "Car", null);
    await withService((tx) =>
      tx.query(`update public.device_pairing_requests set expires_at = now() - interval '1 second' where id = $1`, [req.requestId]),
    );
    await expect(approvePairing(owner, req.token)).rejects.toThrow(/expired/);
    expect(await pairingStatus(owner, req.requestId)).toBe("expired");
  });

  it("invalid credentials and other users' credentials resolve to null", async () => {
    const req = await createPairingRequest(owner, "Car", null);
    await approvePairing(owner, req.token);
    const { credential } = await redeemPairing(owner, req.requestId, req.nonce);
    expect(await resolveDevice(owner, credential.replace(/.$/, "x"))).toBeNull();
    expect(await resolveDevice(other, credential)).toBeNull();
    expect(await resolveDevice(owner, "garbage")).toBeNull();
  });
});

describe("send-to-car inbox", () => {
  it("delivers, survives reconnect (replay), acknowledges and dismisses; revoke blocks", async () => {
    const req = await createPairingRequest(owner, "Inbox car", null);
    await approvePairing(owner, req.token);
    const { deviceId, credential } = await redeemPairing(owner, req.requestId, req.nonce);

    const sent = await sendToDevice(owner, { deviceId, name: "Freedom Square", address: "Tbilisi", lat: 41.6934, lon: 44.8015, note: undefined }, "Phone");
    expect(sent.state).toBe("sent");

    // first poll acknowledges delivery
    const first = await fetchDeviceInbox(owner, deviceId);
    expect(first.map((i) => i.id)).toContain(sent.id);
    expect(first.find((i) => i.id === sent.id)?.state).toBe("received");

    // "reconnect": a later poll still returns it until the user acts
    const replay = await fetchDeviceInbox(owner, deviceId);
    expect(replay.find((i) => i.id === sent.id)).toBeTruthy();

    // another user cannot act on it or see it
    expect(await actOnInbox(other, sent.id, "dismiss")).toBe(false);
    expect(await fetchDeviceInbox(other, deviceId)).toHaveLength(0);

    expect(await actOnInbox(owner, sent.id, "dismiss")).toBe(true);
    expect((await fetchDeviceInbox(owner, deviceId)).find((i) => i.id === sent.id)).toBeUndefined();

    // revoke: credential stops resolving and sending is refused
    await revokeDevice(owner, deviceId);
    expect(await resolveDevice(owner, credential)).toBeNull();
    await expect(
      sendToDevice(owner, { deviceId, name: "X", address: "", lat: 1, lon: 1, note: undefined }, "Phone"),
    ).rejects.toThrow(/not paired|revoked/);
  });

  it("cannot send to another user's device", async () => {
    const req = await createPairingRequest(owner, "Owner car", null);
    await approvePairing(owner, req.token);
    const { deviceId } = await redeemPairing(owner, req.requestId, req.nonce);
    await expect(
      sendToDevice(other, { deviceId, name: "X", address: "", lat: 1, lon: 1, note: undefined }, "Phone"),
    ).rejects.toThrow(/not paired/);
  });
});
