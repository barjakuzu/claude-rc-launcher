import { NextResponse, type NextRequest } from "next/server";
import { env } from "@/lib/env";
import { reconcileSubscriptions } from "@/lib/billing";
import { timingSafeEqualStr } from "@/lib/auth/crypto";
import { withService } from "@/lib/db";

/** Vercel Cron (see vercel.json). Authorised by `Authorization: Bearer $CRON_SECRET`. */
export async function GET(req: NextRequest) {
  const secret = env().CRON_SECRET;
  const auth = req.headers.get("authorization") ?? "";
  if (!secret || !timingSafeEqualStr(auth, `Bearer ${secret}`)) {
    return NextResponse.json({ error: "unauthorized" }, { status: 401 });
  }
  const billing = await reconcileSubscriptions();
  // retention: hard-delete inbox items older than 30 days and expired pairing requests
  const cleaned = await withService(async (tx) => ({
    inbox: (await tx.query(`delete from public.inbox_destinations where sent_at < now() - interval '30 days' returning id`)).length,
    pairing: (await tx.query(`delete from public.device_pairing_requests where expires_at < now() - interval '1 day' returning id`)).length,
  }));
  return NextResponse.json({ billing, cleaned });
}
