import Link from "next/link";
import { Logo } from "@/components/ui/logo";

export function SiteFooter() {
  return (
    <footer className="mx-auto mt-24 w-full max-w-[1440px] px-4 pb-[max(2.5rem,env(safe-area-inset-bottom))] sm:px-6 xl:px-10">
      <div className="flex flex-col gap-5 border-t border-line pt-8 md:flex-row md:items-center">
        <div className="flex items-center gap-4">
          <Logo />
          <span className="hidden h-6 w-px bg-line-strong md:block" aria-hidden />
          <span className="text-sm text-muted">3D navigation for in-car browsers</span>
        </div>
        <nav aria-label="Footer" className="flex flex-wrap gap-x-6 gap-y-2 text-sm text-muted md:ml-auto">
          {[
            ["/features", "Features"],
            ["/pricing", "Pricing"],
            ["/how-it-works", "How it works"],
            ["/faq", "FAQ"],
            ["/contact", "Contact"],
            ["/terms", "Terms"],
            ["/privacy", "Privacy"],
          ].map(([h, l]) => (
            <Link key={h} href={h} className="transition-colors hover:text-text">
              {l}
            </Link>
          ))}
        </nav>
      </div>
      <p className="mt-6 max-w-3xl text-xs leading-relaxed text-faint">
        MAPIO is an independent product and is not affiliated with, endorsed by, or sponsored by Tesla, Inc. “Tesla” is
        used only to describe browser compatibility. MAPIO does not connect to vehicle systems. Always obey traffic laws
        and road signs; you remain responsible for safe driving. Map data © OpenStreetMap contributors and the providers
        credited in the app.
      </p>
    </footer>
  );
}
