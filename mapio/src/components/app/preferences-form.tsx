"use client";

import { useActionState, useEffect } from "react";
import { Loader2 } from "lucide-react";
import type { PrefState } from "@/app/(app)/account/actions";

type Profile = {
  display_name: string | null;
  locale: string;
  distance_unit: string;
  temperature_unit: string;
  time_format: string;
  currency: string;
  theme: string;
  notify_inbox: boolean;
  keep_recent_destinations: boolean;
};

function Seg({ name, value, options, legend }: { name: string; value: string; options: [string, string][]; legend: string }) {
  return (
    <fieldset>
      <legend className="mb-2 text-sm font-semibold">{legend}</legend>
      <div className="grid gap-2" style={{ gridTemplateColumns: `repeat(${options.length}, minmax(0,1fr))` }}>
        {options.map(([v, l]) => (
          <label key={v} className="cursor-pointer">
            <input type="radio" name={name} value={v} defaultChecked={value === v} className="peer sr-only" />
            <span className="flex min-h-12 items-center justify-center rounded-xl border border-line px-3 text-sm font-semibold peer-checked:border-accent peer-checked:bg-accent/20 peer-focus-visible:outline peer-focus-visible:outline-3 peer-focus-visible:outline-accent-2">
              {l}
            </span>
          </label>
        ))}
      </div>
    </fieldset>
  );
}

export function PreferencesForm({
  profile,
  action,
  submitLabel,
  plan,
  compact = false,
}: {
  profile: Profile;
  action: (s: PrefState, f: FormData) => Promise<PrefState>;
  submitLabel: string;
  plan?: string;
  compact?: boolean;
}) {
  const [state, formAction, pending] = useActionState(action, null);
  useEffect(() => {
    if (state?.ok) {
      const theme = (document.querySelector('input[name="theme"]:checked') as HTMLInputElement | null)?.value;
      if (theme) {
        try {
          localStorage.setItem("mapio_theme", theme);
        } catch {}
        const resolved = theme === "system" ? (matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark") : theme;
        document.documentElement.dataset.theme = resolved;
      }
    }
  }, [state]);
  return (
    <form
      action={formAction}
      className="grid gap-5"
      onSubmit={(e) => {
        const theme = (e.currentTarget.elements.namedItem("theme") as RadioNodeList | null)?.value;
        if (theme) try { localStorage.setItem("mapio_theme", theme); } catch {}
      }}
    >
      {plan && <input type="hidden" name="plan" value={plan} />}
      {!compact && (
        <label className="grid gap-1.5 text-sm font-semibold">
          Display name
          <input name="display_name" className="field" defaultValue={profile.display_name ?? ""} maxLength={80} autoComplete="nickname" />
        </label>
      )}
      <div className="grid gap-5 md:grid-cols-2">
        <Seg legend="Language" name="locale" value={profile.locale} options={[["en", "English"], ["ka", "ქართული"]]} />
        <Seg legend="Theme" name="theme" value={profile.theme} options={[["dark", "Dark"], ["light", "Light"], ["system", "System"]]} />
        <Seg legend="Distance" name="distance_unit" value={profile.distance_unit} options={[["km", "Kilometres"], ["mi", "Miles"]]} />
        <Seg legend="Temperature" name="temperature_unit" value={profile.temperature_unit} options={[["c", "°C"], ["f", "°F"]]} />
        <Seg legend="Time" name="time_format" value={profile.time_format} options={[["24h", "24-hour"], ["12h", "12-hour"]]} />
        <Seg legend="Currency" name="currency" value={profile.currency} options={[["GEL", "GEL ₾"], ["USD", "USD $"], ["EUR", "EUR €"]]} />
      </div>
      {!compact && (
        <div className="grid gap-3">
          <label className="flex items-center gap-3">
            <input type="checkbox" name="notify_inbox" defaultChecked={profile.notify_inbox} className="h-5 w-5 accent-[var(--accent)]" />
            Show a notification when a destination arrives
          </label>
          <label className="flex items-center gap-3">
            <input type="checkbox" name="keep_recent_destinations" defaultChecked={profile.keep_recent_destinations} className="h-5 w-5 accent-[var(--accent)]" />
            Keep recent destinations (turning this off deletes the history)
          </label>
        </div>
      )}
      {compact && (
        <>
          <input type="hidden" name="notify_inbox" value="on" />
          <input type="hidden" name="keep_recent_destinations" value="on" />
        </>
      )}
      <div aria-live="polite">
        {state?.error && <p role="alert" className="text-sm text-bad">{state.error}</p>}
        {state?.ok && <p className="text-sm text-ok">Saved.</p>}
      </div>
      <button className="btn btn-primary w-full !min-h-13 md:w-auto md:justify-self-start" disabled={pending}>
        {pending && <Loader2 className="h-4 w-4 animate-spin" aria-hidden />} {submitLabel}
      </button>
    </form>
  );
}

export function DeleteAccountForm({ action, email }: { action: (s: PrefState, f: FormData) => Promise<PrefState>; email: string }) {
  const [state, formAction, pending] = useActionState(action, null);
  return (
    <form action={formAction} className="grid gap-3">
      <p className="text-sm text-muted">
        Permanently deletes your profile, saved places, devices and Send-to-Car history. Any active subscription is set to cancel
        at the end of its period first. This cannot be undone.
      </p>
      <label className="grid gap-1.5 text-sm font-semibold">
        Type <span className="font-mono text-text">{email}</span> to confirm
        <input name="confirm" className="field" autoComplete="off" />
      </label>
      {state?.error && <p role="alert" className="text-sm text-bad">{state.error}</p>}
      <button className="btn btn-danger justify-self-start" disabled={pending}>
        {pending && <Loader2 className="h-4 w-4 animate-spin" aria-hidden />} Delete my account
      </button>
    </form>
  );
}
