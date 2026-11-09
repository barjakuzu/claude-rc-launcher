import type { Metadata } from "next";
import { AuthCard } from "@/components/auth/auth-card";
import { ForgotForm } from "@/components/auth/auth-forms";

export const metadata: Metadata = { title: "Reset password", robots: { index: false } };

export default function ForgotPage() {
  return (
    <AuthCard title="Reset your password" subtitle="We'll email you a secure link.">
      <ForgotForm />
    </AuthCard>
  );
}
