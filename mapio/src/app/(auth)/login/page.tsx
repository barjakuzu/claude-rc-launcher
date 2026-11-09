import type { Metadata } from "next";
import { AuthCard } from "@/components/auth/auth-card";
import { LoginForm } from "@/components/auth/auth-forms";
import { GoogleButton } from "@/components/auth/google-button";
import { sp, type SearchParams } from "@/lib/page";

export const metadata: Metadata = { title: "Log in", robots: { index: false } };

export default async function LoginPage({ searchParams }: { searchParams: SearchParams }) {
  const q = await searchParams;
  return (
    <AuthCard title="Welcome back" subtitle="Log in to MAPIO">
      {sp(q.error) === "oauth_unavailable" && (
        <p role="alert" className="mb-4 rounded-xl border border-bad/50 bg-bad/10 px-4 py-3 text-sm text-bad">Google sign-in is not available.</p>
      )}
      <GoogleButton next={sp(q.next)} />
      <LoginForm next={sp(q.next)} />
    </AuthCard>
  );
}
