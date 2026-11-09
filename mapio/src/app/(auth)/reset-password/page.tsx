import type { Metadata } from "next";
import { AuthCard } from "@/components/auth/auth-card";
import { ResetForm } from "@/components/auth/auth-forms";
import { sp, type SearchParams } from "@/lib/page";

export const metadata: Metadata = { title: "Choose a new password", robots: { index: false } };

export default async function ResetPage({ searchParams }: { searchParams: SearchParams }) {
  const q = await searchParams;
  return (
    <AuthCard title="Choose a new password">
      <ResetForm devToken={sp(q.dev_token)} />
    </AuthCard>
  );
}
