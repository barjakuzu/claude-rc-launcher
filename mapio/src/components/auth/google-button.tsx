import { googleAction } from "@/app/(auth)/actions";
import { authMode } from "@/lib/env";

/** Rendered only when Supabase Auth is configured (Google must also be enabled in Supabase). */
export function GoogleButton({ next }: { next?: string }) {
  let live = false;
  try {
    live = authMode() === "live" && process.env.NEXT_PUBLIC_GOOGLE_AUTH_ENABLED === "true";
  } catch {
    live = false;
  }
  if (!live) return null;
  return (
    <form action={googleAction} className="mb-5">
      <input type="hidden" name="next" value={next ?? ""} />
      <button className="btn btn-ghost w-full">Continue with Google</button>
      <div className="mt-5 flex items-center gap-3 text-xs text-faint">
        <span className="h-px flex-1 bg-line" /> or <span className="h-px flex-1 bg-line" />
      </div>
    </form>
  );
}
