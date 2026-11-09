"use server";

import { revalidatePath } from "next/cache";
import { redirect } from "next/navigation";
import { getSessionUser, deleteAuthUser } from "@/lib/auth";
import { updateProfile } from "@/lib/repos/profiles";
import { profileInput } from "@/lib/validation";
import { getBillingState, scheduleCancel } from "@/lib/billing";

export type PrefState = { ok?: boolean; error?: string } | null;

function readPrefs(form: FormData) {
  return profileInput.safeParse({
    display_name: form.get("display_name") ?? "",
    locale: form.get("locale"),
    distance_unit: form.get("distance_unit"),
    temperature_unit: form.get("temperature_unit"),
    time_format: form.get("time_format"),
    currency: form.get("currency"),
    theme: form.get("theme"),
    notify_inbox: form.get("notify_inbox") === "on",
    keep_recent_destinations: form.get("keep_recent_destinations") === "on",
  });
}

export async function savePreferences(_: PrefState, form: FormData): Promise<PrefState> {
  const user = await getSessionUser();
  if (!user) redirect("/login");
  const p = readPrefs(form);
  if (!p.success) return { error: "Please check the highlighted settings." };
  await updateProfile(user.id, p.data);
  revalidatePath("/", "layout");
  return { ok: true };
}

export async function completeOnboarding(_: PrefState, form: FormData): Promise<PrefState> {
  const user = await getSessionUser();
  if (!user) redirect("/login");
  const p = readPrefs(form);
  if (!p.success) return { error: "Please check your settings." };
  await updateProfile(user.id, p.data, true);
  redirect(form.get("plan") === "premium" ? "/account/billing?start=1" : "/app");
}

export async function deleteAccount(_: PrefState, form: FormData): Promise<PrefState> {
  const user = await getSessionUser();
  if (!user) redirect("/login");
  if (String(form.get("confirm") ?? "").trim().toLowerCase() !== user.email.toLowerCase()) {
    return { error: "Type your email address exactly to confirm." };
  }
  // Stop future renewals first so deleting MAPIO data never leaves an active charge behind.
  const state = await getBillingState(user.id);
  const sub = state.subscription;
  if (sub && ["trialing", "active", "past_due"].includes(sub.status) && sub.scheduled_change_action !== "cancel") {
    try {
      await scheduleCancel(user.id);
    } catch {
      return { error: "We couldn't cancel your subscription automatically. Cancel it in Billing first, then delete your account." };
    }
  }
  await deleteAuthUser(user.id);
  redirect("/?account=deleted");
}
