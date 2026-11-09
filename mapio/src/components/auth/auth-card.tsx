import Link from "next/link";
import { Logo } from "@/components/ui/logo";
import { AmbientBackground } from "@/components/ui/glass";

export function AuthCard({ title, subtitle, children, footer }: { title: string; subtitle?: string; children: React.ReactNode; footer?: React.ReactNode }) {
  return (
    <div className="relative grid min-h-dvh place-items-center px-4 py-[max(2.5rem,env(safe-area-inset-top))]">
      <AmbientBackground intensity="soft" />
      <div className="rise relative z-[1] w-full max-w-[460px]">
        <Link href="/" className="mb-8 flex justify-center" aria-label="MAPIO home">
          <Logo />
        </Link>
        <main id="main" className="glass-elevated glass-accent !rounded-[28px] p-6 sm:p-8">
          <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
          {subtitle && <p className="mt-1 text-muted">{subtitle}</p>}
          <div className="mt-6">{children}</div>
        </main>
        {footer && <div className="mt-6 text-center text-sm text-muted">{footer}</div>}
      </div>
    </div>
  );
}
