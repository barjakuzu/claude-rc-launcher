import Link from "next/link";
import { Logo } from "@/components/ui/logo";

const links = [
  { href: "/features", label: "Features" },
  { href: "/pricing", label: "Pricing" },
  { href: "/how-it-works", label: "How it works" },
  { href: "/faq", label: "FAQ" },
];

/** Floating glass navigation bar, centred in the content width. */
export function SiteHeader({ trialDays = 7 }: { trialDays?: number }) {
  return (
    <header className="sticky top-[max(0.75rem,env(safe-area-inset-top))] z-40 mx-auto w-full max-w-[1440px] px-4">
      <nav aria-label="Main" className="glass flex items-center gap-3 !rounded-full py-2 pl-5 pr-2 sm:gap-5 sm:pl-7">
        <Link href="/" aria-label="MAPIO home" className="flex min-h-11 shrink-0 items-center">
          <Logo />
        </Link>
        <span className="hidden h-6 w-px bg-line-strong lg:block" aria-hidden />
        <ul className="hidden items-center gap-1 lg:flex">
          {links.map((l) => (
            <li key={l.href}>
              <Link href={l.href} className="whitespace-nowrap rounded-full px-4 py-2 text-[15px] font-medium text-text/80 transition-colors hover:text-text">
                {l.label}
              </Link>
            </li>
          ))}
        </ul>
        <div className="ml-auto flex items-center gap-2">
          <Link href="/login" className="btn btn-ghost hidden !min-h-11 sm:inline-flex">
            Log in
          </Link>
          <Link href="/signup" className="btn btn-primary !min-h-11 !px-5 text-[15px]">
            Start {trialDays}-day free trial
          </Link>
        </div>
      </nav>
      <ul className="mt-2 flex flex-wrap justify-center gap-1 lg:hidden">
        {links.map((l) => (
          <li key={l.href}>
            <Link href={l.href} className="whitespace-nowrap rounded-full px-3 py-2 text-sm font-medium text-muted hover:text-text">
              {l.label}
            </Link>
          </li>
        ))}
        <li className="sm:hidden">
          <Link href="/login" className="whitespace-nowrap rounded-full px-3 py-2 text-sm font-semibold text-accent-2">
            Log in
          </Link>
        </li>
      </ul>
    </header>
  );
}
