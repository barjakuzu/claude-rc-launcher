"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Car, CreditCard, Inbox, LayoutGrid, Navigation, Shield, Star, UserRound, Zap } from "lucide-react";

const ICONS = { LayoutGrid, Navigation, Zap, Star, Car, Inbox, CreditCard, UserRound, Shield } as const;
export type NavItem = { href: string; label: string; short?: string; icon: keyof typeof ICONS; badge?: number; dot?: boolean };

/** `exactAccount`: in the sidebar, Billing (/account/billing) and Account (/account) are separate items. */
function useActive(exactAccount: boolean) {
  const path = usePathname();
  return (href: string) =>
    href === "/app" || (exactAccount && href === "/account") ? path === href : path === href || path.startsWith(`${href}/`);
}

/** Desktop sidebar navigation. */
export function NavLinks({ items }: { items: NavItem[] }) {
  const active = useActive(true);
  return (
    <ul className="grid gap-1">
      {items.map((it) => {
        const I = ICONS[it.icon];
        const on = active(it.href);
        return (
          <li key={it.href}>
            <Link
              href={it.href}
              aria-current={on ? "page" : undefined}
              className={`flex min-h-11 items-center gap-3 rounded-2xl px-3.5 text-[15px] font-medium transition-[background-color,color] duration-150 ${
                on
                  ? "bg-gradient-to-b from-[#4596ff] to-[#1f6fff] text-white shadow-[0_10px_24px_-10px_var(--glow),inset_0_1px_0_rgba(255,255,255,0.3)]"
                  : "text-text/80 hover:bg-white/[0.06] hover:text-text"
              }`}
            >
              <I className="h-5 w-5 shrink-0" aria-hidden />
              <span className="flex-1 truncate">{it.label}</span>
              {it.dot && <span className="h-2 w-2 rounded-full bg-ok shadow-[0_0_8px_var(--ok)]" aria-label="connected" />}
              {!!it.badge && (
                <span className="grid h-5 min-w-5 place-items-center rounded-full bg-accent-2/90 px-1.5 text-[11px] font-bold text-[#04223a]">{it.badge}</span>
              )}
            </Link>
          </li>
        );
      })}
    </ul>
  );
}

/**
 * Mobile dock: floats above the iOS home indicator (safe-area aware). Pages reserve space
 * with the `dock-clearance` utility so content and buttons are never covered.
 */
export function BottomNavigation({ items }: { items: NavItem[] }) {
  const active = useActive(false);
  return (
    <nav
      aria-label="App"
      className="glass-strong fixed inset-x-3 z-40 !rounded-[22px] px-1.5 lg:hidden"
      style={{ bottom: "calc(0.5rem + env(safe-area-inset-bottom, 0px))", height: "var(--dock-h)" }}
    >
      <ul className="grid h-full grid-cols-5 items-center">
        {items.slice(0, 5).map((it) => {
          const I = ICONS[it.icon];
          const on = active(it.href);
          return (
            <li key={it.href} className="flex justify-center">
              <Link
                href={it.href}
                aria-current={on ? "page" : undefined}
                className={`flex min-w-[3.75rem] flex-col items-center gap-1 rounded-2xl px-1.5 py-1.5 text-[11px] font-semibold transition-colors ${
                  on ? "bg-accent/15 text-accent-2" : "text-muted hover:text-text"
                }`}
              >
                <span className="relative">
                  <I className="h-[22px] w-[22px]" aria-hidden />
                  {!!it.badge && (
                    <span className="absolute -right-2.5 -top-1.5 grid h-4 min-w-4 place-items-center rounded-full bg-accent-2 px-1 text-[10px] font-bold text-[#04223a]">
                      {it.badge}
                    </span>
                  )}
                </span>
                <span className="whitespace-nowrap leading-none">{it.short ?? it.label}</span>
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
