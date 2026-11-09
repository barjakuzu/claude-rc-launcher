import type { Metadata } from "next";
import Link from "next/link";
import { redirect } from "next/navigation";
import { CheckCircle2, ShieldAlert, Smartphone } from "lucide-react";
import { AuthCard } from "@/components/auth/auth-card";
import { getSessionUser } from "@/lib/auth";
import { approvePairing, PairingError, peekPairingToken } from "@/lib/repos/devices";
import { rateLimit } from "@/lib/ratelimit";

export const metadata: Metadata = { title: "Approve pairing", robots: { index: false, follow: false } };
// Token is in the URL: the global Referrer-Policy (strict-origin-when-cross-origin) keeps it
// out of cross-origin Referer headers, and the page loads no third-party resources.
export const dynamic = "force-dynamic";

type Props = { params: Promise<{ token: string }>; searchParams: Promise<{ done?: string; error?: string }> };

async function approve(token: string) {
  "use server";
  const user = await getSessionUser();
  if (!user) redirect(`/login?next=${encodeURIComponent(`/pair/${token}`)}`);
  if (!rateLimit(`pair-approve:${user.id}`, 10, 10 * 60_000).ok) redirect(`/pair/${token}?error=rate`);
  try {
    await approvePairing(user.id, token);
  } catch (e) {
    const code = e instanceof PairingError ? "invalid" : "server";
    redirect(`/pair/${token}?error=${code}`);
  }
  redirect(`/pair/${token}?done=1`);
}

export default async function PairApprovePage({ params, searchParams }: Props) {
  const { token } = await params;
  const q = await searchParams;
  if (!/^[A-Za-z0-9_-]{20,64}$/.test(token)) {
    return <AuthCard title="Invalid pairing link"><p className="text-muted">Generate a new QR code in your car browser.</p></AuthCard>;
  }
  const user = await getSessionUser();
  if (!user) redirect(`/login?next=${encodeURIComponent(`/pair/${token}`)}`);

  if (q.done) {
    return (
      <AuthCard title="Browser paired">
        <p className="flex items-center gap-2 text-ok"><CheckCircle2 className="h-5 w-5" aria-hidden /> Approved. Your car browser will finish pairing automatically.</p>
        <Link href="/app/map" className="btn btn-primary mt-6 w-full">Find a destination to send</Link>
      </AuthCard>
    );
  }
  const peek = await peekPairingToken(user.id, token);
  const problem =
    q.error === "rate" ? "Too many attempts. Wait a few minutes."
    : peek.state === "invalid" ? "This pairing code is invalid."
    : peek.state === "other_account" ? "This code was created by a different MAPIO account. Sign in with the same account on both devices."
    : peek.state === "used" ? "This code has already been used."
    : peek.state === "expired" ? "This code has expired. Create a new one in your car browser."
    : q.error ? "This code can no longer be approved." : null;

  if (problem) {
    return (
      <AuthCard title="Can't approve this code">
        <p className="flex items-start gap-2 text-warn"><ShieldAlert className="mt-0.5 h-5 w-5 shrink-0" aria-hidden /> {problem}</p>
        <p className="mt-4 text-sm text-muted">Signed in as {user.email}</p>
      </AuthCard>
    );
  }
  return (
    <AuthCard title="Approve this browser?" subtitle={`Signed in as ${user.email}`}>
      <div className="flex items-center gap-3 rounded-2xl border border-line p-4">
        <Smartphone className="h-6 w-6 text-accent-2" aria-hidden />
        <div>
          <p className="font-semibold">{peek.deviceName}</p>
          <p className="text-sm text-muted">will receive destinations you send from MAPIO.</p>
        </div>
      </div>
      <p className="mt-4 text-sm text-muted">Only approve if you just created this code in your own car&apos;s browser.</p>
      <form action={approve.bind(null, token)} className="mt-6">
        <button className="btn btn-primary w-full !min-h-14 text-lg">Approve pairing</button>
      </form>
    </AuthCard>
  );
}
