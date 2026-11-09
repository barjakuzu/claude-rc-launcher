"use server";

import { headers } from "next/headers";
import { redirect } from "next/navigation";
import { z } from "zod";
import {
  completePasswordReset,
  requestPasswordReset,
  safeNext,
  signIn,
  signOut,
  signUp,
  startGoogleOAuth,
} from "@/lib/auth";
import { credentials } from "@/lib/validation";
import { rateLimit } from "@/lib/ratelimit";
import { ConfigError } from "@/lib/env";

export type FormState = { error?: string; info?: string; devLink?: string } | null;

async function clientIp() {
  const h = await headers();
  return (h.get("x-forwarded-for") ?? "").split(",")[0].trim() || h.get("x-real-ip") || "local";
}

function friendly(e: unknown): string {
  if (e instanceof ConfigError) return "Sign-in is not configured on this server yet.";
  return "Something went wrong. Please try again.";
}

export async function loginAction(_: FormState, form: FormData): Promise<FormState> {
  const parsed = credentials.safeParse({ email: form.get("email"), password: form.get("password") });
  if (!parsed.success) return { error: "Enter a valid email and password." };
  const rl = rateLimit(`login:${await clientIp()}:${parsed.data.email}`, 10, 10 * 60_000);
  if (!rl.ok) return { error: `Too many attempts. Try again in ${Math.ceil(rl.retryAfterS / 60)} min.` };
  let res;
  try {
    res = await signIn(parsed.data.email, parsed.data.password);
  } catch (e) {
    return { error: friendly(e) };
  }
  if (!res.ok) return { error: res.error };
  redirect(safeNext(String(form.get("next") ?? ""), "/app"));
}

export async function signupAction(_: FormState, form: FormData): Promise<FormState> {
  const parsed = credentials.safeParse({ email: form.get("email"), password: form.get("password") });
  if (!parsed.success) return { error: parsed.error.issues[0]?.message ?? "Check your email and password." };
  if (form.get("terms") !== "on") return { error: "Please accept the Terms and Privacy Policy." };
  const rl = rateLimit(`signup:${await clientIp()}`, Number(process.env.SIGNUP_LIMIT_PER_HOUR ?? 5), 60 * 60_000);
  if (!rl.ok) return { error: "Too many sign-ups from this network. Try again later." };
  let res;
  try {
    res = await signUp(parsed.data.email, parsed.data.password);
  } catch (e) {
    return { error: friendly(e) };
  }
  if (!res.ok) return { error: res.error };
  if (res.needsEmailConfirmation) return { info: "Check your inbox to confirm your email, then sign in." };
  const plan = form.get("plan") === "premium" ? "?plan=premium" : "";
  redirect(`/onboarding${plan}`);
}

export async function forgotAction(_: FormState, form: FormData): Promise<FormState> {
  const email = z.string().email().safeParse(form.get("email"));
  if (!email.success) return { error: "Enter a valid email." };
  const rl = rateLimit(`forgot:${await clientIp()}`, 5, 30 * 60_000);
  if (!rl.ok) return { error: "Too many requests. Try again later." };
  try {
    const r = await requestPasswordReset(email.data);
    return {
      info: "If an account exists for that email, we've sent a reset link.",
      devLink: r.devResetLink,
    };
  } catch (e) {
    return { error: friendly(e) };
  }
}

export async function resetAction(_: FormState, form: FormData): Promise<FormState> {
  const pw = credentials.shape.password.safeParse(form.get("password"));
  if (!pw.success) return { error: pw.error.issues[0]?.message };
  const res = await completePasswordReset(pw.data, String(form.get("dev_token") ?? "") || undefined);
  if (!res.ok) return { error: res.error };
  redirect("/app");
}

export async function googleAction(form: FormData) {
  const url = await startGoogleOAuth(safeNext(String(form.get("next") ?? ""), "/app"));
  redirect(url ?? "/login?error=oauth_unavailable");
}

export async function signOutAction() {
  await signOut();
  redirect("/");
}
