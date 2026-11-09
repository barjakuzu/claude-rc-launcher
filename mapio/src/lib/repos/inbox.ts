import "server-only";
import type { z } from "zod";
import { withUser } from "@/lib/db";
import type { sendToCarInput } from "@/lib/validation";

export interface InboxItem {
  id: string;
  target_device_id: string;
  name: string;
  address: string | null;
  lat: number;
  lon: number;
  note: string | null;
  sender_label: string | null;
  state: "sent" | "received" | "opened" | "navigating" | "dismissed";
  sent_at: string;
  received_at: string | null;
  acted_at: string | null;
  device_name?: string;
}

export async function sendToDevice(userId: string, input: z.infer<typeof sendToCarInput>, senderLabel: string) {
  return withUser(userId, async (tx) => {
    const [dev] = await tx.query<{ id: string }>(
      `select id from public.paired_devices where id = $1 and revoked_at is null`,
      [input.deviceId],
    );
    if (!dev) throw new Error("That device is not paired to your account (or was revoked).");
    const [row] = await tx.query<InboxItem>(
      `insert into public.inbox_destinations (user_id, target_device_id, name, address, lat, lon, note, sender_label)
       values ($1,$2,$3,$4,$5,$6,$7,$8) returning *`,
      [userId, input.deviceId, input.name, input.address || null, input.lat, input.lon, input.note || null, senderLabel],
    );
    return row;
  });
}

/**
 * Inbox for one paired device. Anything not dismissed in the last 7 days is returned,
 * so items survive refreshes/disconnects (replay). Newly fetched "sent" items are
 * acknowledged as "received".
 */
export async function fetchDeviceInbox(userId: string, deviceId: string) {
  return withUser(userId, async (tx) => {
    await tx.query(
      `update public.inbox_destinations set state = 'received', received_at = now()
       where target_device_id = $1 and state = 'sent'`,
      [deviceId],
    );
    return tx.query<InboxItem>(
      `select * from public.inbox_destinations
       where target_device_id = $1 and state <> 'dismissed' and sent_at > now() - interval '7 days'
       order by sent_at desc limit 50`,
      [deviceId],
    );
  });
}

/** Everything the user has sent (for the phone/desktop "Sent" list and /app/inbox). */
export function listAllInbox(userId: string, limit = 50) {
  return withUser(userId, (tx) =>
    tx.query<InboxItem>(
      `select i.*, d.name as device_name from public.inbox_destinations i
       join public.paired_devices d on d.id = i.target_device_id
       order by i.sent_at desc limit $1`,
      [limit],
    ),
  );
}

export async function actOnInbox(userId: string, id: string, action: "open" | "navigate" | "dismiss") {
  const state = action === "open" ? "opened" : action === "navigate" ? "navigating" : "dismissed";
  const rows = await withUser(userId, (tx) =>
    tx.query<{ id: string }>(
      `update public.inbox_destinations set state = $2, acted_at = now(),
         received_at = coalesce(received_at, now())
       where id = $1 returning id`,
      [id, state],
    ),
  );
  return rows.length > 0;
}

export async function unreadCount(userId: string) {
  const [r] = await withUser(userId, (tx) =>
    tx.query<{ n: number }>(`select count(*)::int as n from public.inbox_destinations where state in ('sent','received')`),
  );
  return r?.n ?? 0;
}
