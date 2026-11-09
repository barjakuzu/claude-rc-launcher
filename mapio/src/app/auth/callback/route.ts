import { NextResponse, type NextRequest } from "next/server";
import { exchangeAuthCode, safeNext } from "@/lib/auth";

/** Supabase email-confirmation / OAuth / recovery redirect target. */
export async function GET(request: NextRequest) {
  const url = request.nextUrl;
  const code = url.searchParams.get("code");
  const next = safeNext(url.searchParams.get("next"), "/app");
  if (code && (await exchangeAuthCode(code))) {
    return NextResponse.redirect(new URL(next, url.origin));
  }
  return NextResponse.redirect(new URL("/login?error=callback", url.origin));
}
