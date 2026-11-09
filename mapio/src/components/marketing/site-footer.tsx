import Link from "next/link";
import { Logo } from "@/components/ui/logo";

export function SiteFooter() {
  return (
    <footer className="mx-auto mt-24 w-full max-w-[1280px] px-4 pb-10">
      <div className="flex flex-col gap-6 border-t border-line pt-8 md:flex-row md:items-center">
        <div className="flex items-center gap-4">
          <Logo />
          <span className="hidden h-6 w-px bg-line-strong md:block" aria-hidden />
          <span className="text-sm text-muted">3D navigation for in-car browsers</span>
        </div>
        <nav aria-label="Footer" className="flex flex-wrap gap-x-6 gap-y-2 text-sm text-muted md:ml-auto">
          <Link href="/features" className="hover:text-text">Features</Link>
          <Link href="/pricing" className="hover:text-text">Pricing</Link>
          <Link href="/how-it-works" className="hover:text-text">How it works</Link>
          <Link href="/faq" className="hover:text-text">FAQ</Link>
          <Link href="/contact" className="hover:text-text">Contact</Link>
          <Link href="/terms" className="hover:text-text">Terms</Link>
          <Link href="/privacy" className="hover:text-text">Privacy</Link>
        </nav>
      </div>
      <p className="mt-6 max-w-3xl text-xs leading-relaxed text-faint">
        MAPIO is an independent product and is not affiliated with, endorsed by, or sponsored by Tesla, Inc.
        “Tesla” is used only to describe browser compatibility. MAPIO does not connect to vehicle systems.
        Always obey traffic laws and road signs; you remain responsible for safe driving. Map data © OpenStreetMap
        contributors and the map providers credited in the app.
      </p>
    </footer>
  );
}
