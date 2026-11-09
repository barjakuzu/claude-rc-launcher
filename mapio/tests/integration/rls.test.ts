import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { closeDb, withService, withUser } from "@/lib/db";
import { createUser } from "../support/db";

let a: string;
let b: string;

beforeAll(async () => {
  a = await createUser("rls-a@example.test");
  b = await createUser("rls-b@example.test");
  await withService(async (tx) => {
    await tx.query(`insert into public.saved_places (user_id, label, name, lat, lon) values ($1,'Home','A home',41.7,44.8)`, [a]);
    await tx.query(
      `insert into public.subscriptions (user_id, provider, provider_subscription_id, status) values ($1,'dev','sub_a','active')`,
      [a],
    );
  });
});
afterAll(closeDb);

describe("Row Level Security (real migrations on PGlite)", () => {
  it("creates a profile for each new auth user via trigger", async () => {
    const rows = await withUser(a, (tx) => tx.query(`select id, role from public.profiles`));
    expect(rows).toEqual([{ id: a, role: "user" }]);
  });

  it("users cannot read each other's saved places or subscriptions", async () => {
    expect(await withUser(b, (tx) => tx.query(`select * from public.saved_places`))).toHaveLength(0);
    expect(await withUser(b, (tx) => tx.query(`select * from public.subscriptions`))).toHaveLength(0);
    expect(await withUser(a, (tx) => tx.query(`select * from public.saved_places`))).toHaveLength(1);
  });

  it("users cannot write rows for another user", async () => {
    await expect(
      withUser(b, (tx) =>
        tx.query(`insert into public.saved_places (user_id, label, name, lat, lon) values ($1,'x','x',1,1)`, [a]),
      ),
    ).rejects.toThrow(/row-level security/);
  });

  it("users cannot update or delete another user's rows (silently zero rows)", async () => {
    const upd = await withUser(b, (tx) => tx.query(`update public.saved_places set label = 'pwned' returning id`));
    const del = await withUser(b, (tx) => tx.query(`delete from public.saved_places returning id`));
    expect(upd).toHaveLength(0);
    expect(del).toHaveLength(0);
  });

  it("users cannot escalate their own role", async () => {
    await expect(withUser(a, (tx) => tx.query(`update public.profiles set role = 'admin' where id = $1`, [a]))).rejects.toThrow(
      /permission denied/,
    );
  });

  it("users cannot write entitlements", async () => {
    await expect(
      withUser(a, (tx) => tx.query(`update public.subscriptions set status = 'active' where user_id = $1`, [a])),
    ).rejects.toThrow(/permission denied/);
    await expect(
      withUser(b, (tx) =>
        tx.query(
          `insert into public.subscriptions (user_id, provider, provider_subscription_id, status) values ($1,'dev','free','active')`,
          [b],
        ),
      ),
    ).rejects.toThrow(/permission denied/);
  });

  it("billing event log and trial claims are not readable by users", async () => {
    await expect(withUser(a, (tx) => tx.query(`select * from public.processed_billing_events`))).rejects.toThrow(
      /permission denied/,
    );
    await expect(withUser(a, (tx) => tx.query(`select * from public.trial_claims`))).rejects.toThrow(/permission denied/);
  });

  it("cannot send to a device owned by another user", async () => {
    const [dev] = await withService((tx) =>
      tx.query<{ id: string }>(
        `insert into public.paired_devices (user_id, name, secret_hash) values ($1,'A car','x') returning id`,
        [a],
      ),
    );
    await expect(
      withUser(b, (tx) =>
        tx.query(
          `insert into public.inbox_destinations (user_id, target_device_id, name, lat, lon) values ($1,$2,'x',1,1)`,
          [b, dev.id],
        ),
      ),
    ).rejects.toThrow(/row-level security/);
  });

  it("rejects out-of-range coordinates at the database level", async () => {
    await expect(
      withUser(a, (tx) =>
        tx.query(`insert into public.saved_places (user_id, label, name, lat, lon) values ($1,'x','x',91,0)`, [a]),
      ),
    ).rejects.toThrow(/check constraint/);
  });
});
