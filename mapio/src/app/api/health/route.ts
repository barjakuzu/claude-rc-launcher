import { NextResponse } from "next/server";
import { withService } from "@/lib/db";

/** Liveness + database check for uptime monitors. No secrets or config details. */
export async function GET() {
  try {
    await withService((tx) => tx.query("select 1"));
    return NextResponse.json({ ok: true });
  } catch {
    return NextResponse.json({ ok: false }, { status: 503 });
  }
}
