import type { Metadata } from "next";
import { AuthCard } from "@/components/auth/auth-card";
import { SignupForm } from "@/components/auth/auth-forms";
import { GoogleButton } from "@/components/auth/google-button";
import { sp, type SearchParams } from "@/lib/page";

export const metadata: Metadata = { title: "Create your account", robots: { index: false } };

export default async function SignupPage({ searchParams }: { searchParams: SearchParams }) {
  const q = await searchParams;
  return (
    <AuthCard
      title="Create your MAPIO account"
      subtitle="Start with your account — you'll choose your trial next."
      footer="By creating an account you confirm you are at least 18 years old."
    >
      <GoogleButton next="/onboarding" />
      <SignupForm plan={sp(q.plan)} />
    </AuthCard>
  );
}
