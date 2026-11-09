import Link from "next/link";
import { AuthCard } from "@/components/auth/auth-card";

export default function NotFound() {
  return (
    <AuthCard title="Page not found" subtitle="The page you're looking for doesn't exist or has moved.">
      <Link href="/" className="btn btn-primary w-full">Go home</Link>
    </AuthCard>
  );
}
