import { NextResponse, type NextRequest } from "next/server";
import { createServerClient } from "@supabase/ssr";

const PRIVATE_PREFIXES = ["/app", "/drive", "/account", "/admin", "/onboarding"];

function isPrivate(pathname: string) {
  return PRIVATE_PREFIXES.some((p) => pathname === p || pathname.startsWith(`${p}/`));
}

/**
 * Proxy (formerly middleware):
 *  1. refreshes the Supabase auth session cookie when Supabase is configured;
 *  2. optimistic redirect to /login for private pages without any session cookie
 *     (the authoritative check is requireUser() in each page / API handler);
 *  3. marks private pages noindex and exposes the path to server components.
 */
export async function proxy(request: NextRequest) {
  const { pathname, search } = request.nextUrl;
  const reqHeaders = new Headers(request.headers);
  reqHeaders.set("x-mapio-path", pathname + search);

  let response = NextResponse.next({ request: { headers: reqHeaders } });

  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const key = process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY;
  let hasSession = request.cookies.has("mapio_dev_session");

  if (url && key) {
    const supabase = createServerClient(url, key, {
      cookies: {
        getAll: () => request.cookies.getAll(),
        setAll: (list, headers) => {
          list.forEach(({ name, value }) => request.cookies.set(name, value));
          response = NextResponse.next({ request: { headers: reqHeaders } });
          list.forEach(({ name, value, options }) => response.cookies.set(name, value, options));
          Object.entries(headers ?? {}).forEach(([k, v]) => response.headers.set(k, v));
        },
      },
    });
    const { data } = await supabase.auth.getClaims();
    hasSession = Boolean(data?.claims?.sub);
  }

  if (isPrivate(pathname)) {
    if (!hasSession) {
      const login = request.nextUrl.clone();
      login.pathname = "/login";
      login.search = `?next=${encodeURIComponent(pathname + search)}`;
      return NextResponse.redirect(login);
    }
    response.headers.set("X-Robots-Tag", "noindex, nofollow");
    response.headers.set("Cache-Control", "private, no-store");
  }
  return response;
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico|brand/|preview/|robots.txt|sitemap.xml|api/webhooks).*)"],
};
