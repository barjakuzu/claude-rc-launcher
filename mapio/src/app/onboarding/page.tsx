import type { Metadata } from "next";
import { AuthCard } from "@/components/auth/auth-card";
import { PreferencesForm } from "@/components/app/preferences-form";
import { getAppContext } from "@/lib/app-context";
import { completeOnboarding } from "@/app/(app)/account/actions";
import { sp, type SearchParams } from "@/lib/page";

export const metadata: Metadata = { title: "Welcome", robots: { index: false } };

export default async function OnboardingPage({ searchParams }: { searchParams: SearchParams }) {
  const ctx = await getAppContext();
  const q = await searchParams;
  return (
    <AuthCard title="Welcome to MAPIO" subtitle="Set your language and units. Defaults are tuned for Georgia — change anything later in Account.">
      <PreferencesForm profile={ctx.profile} action={completeOnboarding} submitLabel="Continue" plan={sp(q.plan)} compact />
    </AuthCard>
  );
}
