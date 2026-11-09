import "server-only";
import { cookies, headers } from "next/headers";
import { redirect } from "next/navigation";
import { createServerClient } from "@supabase/ssr";
import { authMode, env, sessionSecret, isProduction, adminEmails } from "@/lib/env";
import { withService } from "@/lib/db";
import { hashPassword, verifyPassword, signPayload, verifyPayload, randomToken, sha256 } from "./crypto";

export type SessionUser = { id: string; email: string };
export type AuthResult = { ok: true; needsEmailConfirmation?: boolean } | { ok: false; error: string };

export const DEV_SESSION_COOKIE = "mapio_dev_session";
const DEV_SESSION_TTL_S = 60 * 60 * 24 * 14;

// ── Supabase (live) ────────────────────────────────────────────────────────
async function supabaseServer() {
  const store = await cookies();
  const e = env();
  return createServerClient(e.NEXT_PUBLIC_SUPABASE_URL!, e.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY!, {
    cookies: {
      getAll: () => store.getAll(),
      setAll: (list) => {
        try {
          list.forEach(({ name, value, options }) => store.set(name, value, options));
        } catch {
          // Called from a Server Component: proxy.ts refreshes the session instead.
        }
      },
    },
  });
}

// ── Dev adapter ────────────────────────────────────────────────────────────
type DevSession = { uid: string; email: string; exp: number };

async function setDevSession(user: SessionUser) {
  const token = signPayload(sessionSecret(), {
    uid: user.id,
    email: user.email,
    exp: Math.floor(Date.now() / 1000) + DEV_SESSION_TTL_S,
  } satisfies DevSession);
  (await cookies()).set(DEV_SESSION_COOKIE, token, {
    httpOnly: true,
    sameSite: "lax",
    secure: isProduction(),
    path: "/",
    maxAge: DEV_SESSION_TTL_S,
  });
}

// ── Public API ─────────────────────────────────────────────────────────────
export async function getSessionUser(): Promise<SessionUser | null> {
  if (authMode() === "live") {
    const sb = await supabaseServer();
    const { data, error } = await sb.auth.getClaims();
    const claims = data?.claims;
    if (error || !claims?.sub) return null;
    return { id: String(claims.sub), email: String(claims.email ?? "") };
  }
  const s = verifyPayload<DevSession>(sessionSecret(), (await cookies()).get(DEV_SESSION_COOKIE)?.value);
  if (!s || s.exp < Date.now() / 1000) return null;
  // Make sure the account still exists (deleted accounts lose access immediately).
  const rows = await withService((tx) => tx.query(`select id from auth.users where id = $1`, [s.uid]));
  return rows.length ? { id: s.uid, email: s.email } : null;
}

/** For pages: redirect to /login (with return path) when signed out. */
export async function requireUser(returnTo?: string): Promise<SessionUser> {
  const user = await getSessionUser();
  if (!user) {
    const next = returnTo ?? (await headers()).get("x-mapio-path") ?? "/app";
    redirect(`/login?next=${encodeURIComponent(next)}`);
  }
  return user;
}

export async function signUp(email: string, password: string): Promise<AuthResult> {
  email = email.trim().toLowerCase();
  if (authMode() === "live") {
    const sb = await supabaseServer();
    const { data, error } = await sb.auth.signUp({
      email,
      password,
      options: { emailRedirectTo: `${env().APP_URL}/auth/callback?next=/onboarding` },
    });
    if (error) return { ok: false, error: error.message };
    return { ok: true, needsEmailConfirmation: !data.session };
  }
  const existing = await withService((tx) => tx.query(`select 1 from auth.users where email = $1`, [email]));
  if (existing.length) return { ok: false, error: "An account with this email already exists." };
  const [row] = await withService((tx) =>
    tx.query<{ id: string }>(
      `insert into auth.users (email, encrypted_password, email_confirmed_at) values ($1, $2, now()) returning id`,
      [email, hashPassword(password)],
    ),
  );
  await setDevSession({ id: row.id, email });
  await bootstrapAdmin(row.id, email);
  return { ok: true };
}

export async function signIn(email: string, password: string): Promise<AuthResult> {
  email = email.trim().toLowerCase();
  const generic = "Incorrect email or password.";
  if (authMode() === "live") {
    const sb = await supabaseServer();
    const { data, error } = await sb.auth.signInWithPassword({ email, password });
    if (error || !data.user) return { ok: false, error: generic };
    await bootstrapAdmin(data.user.id, email);
    return { ok: true };
  }
  const [row] = await withService((tx) =>
    tx.query<{ id: string; encrypted_password: string | null }>(
      `select id, encrypted_password from auth.users where email = $1`,
      [email],
    ),
  );
  if (!row?.encrypted_password || !verifyPassword(password, row.encrypted_password)) {
    return { ok: false, error: generic };
  }
  await setDevSession({ id: row.id, email });
  await bootstrapAdmin(row.id, email);
  return { ok: true };
}

export async function signOut() {
  if (authMode() === "live") {
    const sb = await supabaseServer();
    await sb.auth.signOut();
  }
  (await cookies()).delete(DEV_SESSION_COOKIE);
}

/**
 * Starts password reset. Live: Supabase sends the email. Dev: there is no mailer,
 * so the reset link is returned to be shown on screen, clearly labelled as dev-only.
 */
export async function requestPasswordReset(email: string): Promise<{ devResetLink?: string }> {
  email = email.trim().toLowerCase();
  if (authMode() === "live") {
    const sb = await supabaseServer();
    await sb.auth.resetPasswordForEmail(email, {
      redirectTo: `${env().APP_URL}/auth/callback?next=/reset-password`,
    });
    return {};
  }
  const [row] = await withService((tx) => tx.query<{ id: string }>(`select id from auth.users where email = $1`, [email]));
  if (!row) return {}; // do not reveal whether the account exists
  const token = randomToken();
  devResetTokens.set(sha256(token), { uid: row.id, email, exp: Date.now() + 30 * 60_000 });
  return { devResetLink: `/reset-password?dev_token=${token}` };
}
const devResetTokens = new Map<string, { uid: string; email: string; exp: number }>();

export async function completePasswordReset(newPassword: string, devToken?: string): Promise<AuthResult> {
  if (authMode() === "live") {
    const sb = await supabaseServer();
    const { error } = await sb.auth.updateUser({ password: newPassword });
    return error ? { ok: false, error: error.message } : { ok: true };
  }
  const entry = devToken ? devResetTokens.get(sha256(devToken)) : undefined;
  if (!entry || entry.exp < Date.now()) return { ok: false, error: "This reset link is invalid or expired." };
  devResetTokens.delete(sha256(devToken!));
  await withService((tx) =>
    tx.query(`update auth.users set encrypted_password = $2 where id = $1`, [entry.uid, hashPassword(newPassword)]),
  );
  await setDevSession({ id: entry.uid, email: entry.email });
  return { ok: true };
}

/** OAuth / magic-link / recovery callback (live mode only). */
export async function exchangeAuthCode(code: string): Promise<boolean> {
  if (authMode() !== "live") return false;
  const sb = await supabaseServer();
  const { error } = await sb.auth.exchangeCodeForSession(code);
  return !error;
}

export async function startGoogleOAuth(next: string): Promise<string | null> {
  if (authMode() !== "live") return null;
  const sb = await supabaseServer();
  const { data, error } = await sb.auth.signInWithOAuth({
    provider: "google",
    options: { redirectTo: `${env().APP_URL}/auth/callback?next=${encodeURIComponent(next)}` },
  });
  return error ? null : data.url;
}

/** Deletes the auth user (cascades to all user-owned rows). */
export async function deleteAuthUser(userId: string) {
  if (authMode() === "live") {
    const e = env();
    if (!e.SUPABASE_SECRET_KEY) throw new Error("SUPABASE_SECRET_KEY is required to delete accounts.");
    const { createClient } = await import("@supabase/supabase-js");
    const admin = createClient(e.NEXT_PUBLIC_SUPABASE_URL!, e.SUPABASE_SECRET_KEY, {
      auth: { persistSession: false },
    });
    const { error } = await admin.auth.admin.deleteUser(userId);
    if (error) throw error;
  } else {
    await withService((tx) => tx.query(`delete from auth.users where id = $1`, [userId]));
  }
  await signOut();
}

/** Promote configured ADMIN_EMAILS on sign-in, with an audit entry. */
async function bootstrapAdmin(userId: string, email: string) {
  if (!adminEmails().includes(email)) return;
  await withService(async (tx) => {
    const r = await tx.query(
      `update public.profiles set role = 'admin' where id = $1 and role <> 'admin' returning id`,
      [userId],
    );
    if (r.length) {
      await tx.query(
        `insert into public.admin_audit_log (actor_id, action, target_user_id, details)
         values (null, 'role.bootstrap_admin', $1, '{"source":"ADMIN_EMAILS"}')`,
        [userId],
      );
    }
  });
}

/** Safe post-login redirect: only same-origin relative paths. */
export function safeNext(next: string | null | undefined, fallback = "/app"): string {
  if (!next || !next.startsWith("/") || next.startsWith("//") || next.includes("\\")) return fallback;
  return next;
}
