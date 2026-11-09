import { NextResponse, type NextRequest } from "next/server";
import { handleWebhook } from "@/lib/billing";

/**
 * Paddle Billing notification destination. Signature verified on the RAW body.
 * Not wrapped in route(): Paddle is a cross-origin server caller.
 * 2xx tells Paddle not to retry; 5xx triggers Paddle's retry schedule.
 */
export async function POST(req: NextRequest) {
  const raw = await req.text();
  if (raw.length > 512_000) return NextResponse.json({ error: "too large" }, { status: 413 });
  try {
    const r = await handleWebhook(raw, req.headers.get("paddle-signature"), "paddle");
    if (r.status !== 200) console.warn("[paddle-webhook]", r.status, r.error);
    return NextResponse.json({ outcome: r.outcome ?? null, error: r.error ?? null }, { status: r.status });
  } catch (e) {
    console.error("[paddle-webhook] processing failed", e);
    return NextResponse.json({ error: "processing failed" }, { status: 500 });
  }
}
