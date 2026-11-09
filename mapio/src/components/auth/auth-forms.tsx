"use client";

import Link from "next/link";
import { useActionState } from "react";
import { Loader2 } from "lucide-react";
import {
  forgotAction,
  loginAction,
  resetAction,
  signupAction,
  type FormState,
} from "@/app/(auth)/actions";

function Alert({ state }: { state: FormState }) {
  if (!state) return null;
  return (
    <div aria-live="polite" className="grid gap-2">
      {state.error && <p role="alert" className="rounded-xl border border-bad/50 bg-bad/10 px-4 py-3 text-sm text-bad">{state.error}</p>}
      {state.info && <p className="rounded-xl border border-ok/40 bg-ok/10 px-4 py-3 text-sm text-ok">{state.info}</p>}
      {state.devLink && (
        <p className="rounded-xl border border-warn/50 bg-warn/10 px-4 py-3 text-sm text-warn">
          Development mode has no email sender. Reset link (dev only):{" "}
          <Link className="font-semibold underline" href={state.devLink}>open reset page</Link>
        </p>
      )}
    </div>
  );
}

function Submit({ pending, children }: { pending: boolean; children: React.ReactNode }) {
  return (
    <button type="submit" className="btn btn-primary w-full !min-h-13 text-base" disabled={pending}>
      {pending && <Loader2 className="h-5 w-5 animate-spin" aria-hidden />} {children}
    </button>
  );
}

function Field(props: React.InputHTMLAttributes<HTMLInputElement> & { label: string }) {
  const { label, ...rest } = props;
  return (
    <label className="grid gap-1.5 text-sm font-medium">
      {label}
      <input className="field text-base" {...rest} />
    </label>
  );
}

export function LoginForm({ next }: { next?: string }) {
  const [state, action, pending] = useActionState(loginAction, null);
  return (
    <form action={action} className="grid gap-4">
      <input type="hidden" name="next" value={next ?? ""} />
      <Field label="Email" name="email" type="email" autoComplete="email" required />
      <Field label="Password" name="password" type="password" autoComplete="current-password" required minLength={8} />
      <Alert state={state} />
      <Submit pending={pending}>Log in</Submit>
      <div className="flex justify-between text-sm">
        <Link href="/forgot-password" className="text-accent-2 hover:underline">Forgot password?</Link>
        <Link href="/signup" className="text-accent-2 hover:underline">Create account</Link>
      </div>
    </form>
  );
}

export function SignupForm({ plan }: { plan?: string }) {
  const [state, action, pending] = useActionState(signupAction, null);
  return (
    <form action={action} className="grid gap-4">
      <input type="hidden" name="plan" value={plan ?? ""} />
      <Field label="Email" name="email" type="email" autoComplete="email" required />
      <Field label="Password" name="password" type="password" autoComplete="new-password" required minLength={8} />
      <label className="flex items-start gap-3 text-sm text-muted">
        <input type="checkbox" name="terms" required className="mt-1 h-5 w-5 accent-[var(--accent)]" />
        <span>
          I agree to the <Link href="/terms" className="text-accent-2 underline">Terms</Link> and{" "}
          <Link href="/privacy" className="text-accent-2 underline">Privacy Policy</Link>.
        </span>
      </label>
      <Alert state={state} />
      <Submit pending={pending}>Create account</Submit>
      <p className="text-center text-sm text-muted">
        Already have an account? <Link href="/login" className="text-accent-2 hover:underline">Log in</Link>
      </p>
    </form>
  );
}

export function ForgotForm() {
  const [state, action, pending] = useActionState(forgotAction, null);
  return (
    <form action={action} className="grid gap-4">
      <Field label="Email" name="email" type="email" autoComplete="email" required />
      <Alert state={state} />
      <Submit pending={pending}>Send reset link</Submit>
      <Link href="/login" className="text-center text-sm text-accent-2 hover:underline">Back to log in</Link>
    </form>
  );
}

export function ResetForm({ devToken }: { devToken?: string }) {
  const [state, action, pending] = useActionState(resetAction, null);
  return (
    <form action={action} className="grid gap-4">
      <input type="hidden" name="dev_token" value={devToken ?? ""} />
      <Field label="New password" name="password" type="password" autoComplete="new-password" required minLength={8} />
      <Alert state={state} />
      <Submit pending={pending}>Set new password</Submit>
    </form>
  );
}
