"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Car, CreditCard, Inbox, LayoutGrid, Navigation, Shield, Star, UserRound, Zap } from "lucide-react";

const ICONS = { LayoutGrid, Navigation, Zap, Star, Car, Inbox, CreditCard, UserRound, Shield } as const;
export type NavItem = { href: string; label: string; short?: string; icon: keyof typeof ICONS; badge?: number; dot?: boolean };

export function NavLinks({ items, variant }: { items: NavItem[]; variant: "side" | "bottom" }) {
  const path = usePathname();
  const active = (href: string) => (href === "/app" ? path === "/app" : path === href || path.startsWith(`${href}/`));
  if (variant === "bottom") {
    return (
      <ul className="grid grid-cols-5">
        {items.slice(0, 5).map((it) => {
          const I = ICONS[it.icon];
          return (
            <li key={it.href}>
              <Link
                href={it.href}
                aria-current={active(it.href) ? "page" : undefined}
                className={`flex flex-col items-center gap-1 py-2 text-[11px] font-medium ${active(it.href) ? "text-accent-2" : "text-muted"}`}
              >
                <span className="relative">
                  <I className="h-6 w-6" aria-hidden />
                  {!!it.badge && <span className="absolute -right-2 -top-1 grid h-4 min-w-4 place-items-center rounded-full bg-accent px-1 text-[10px] text-white">{it.badge}</span>}
                </span>
                {it.short ?? it.label}
              </Link>
            </li>
          );
        })}
      </ul>
    );
  }
  return (
    <ul className="grid gap-1.5">
      {items.map((it) => {
        const I = ICONS[it.icon];
        const on = active(it.href);
        return (
          <li key={it.href}>
            <Link
              href={it.href}
              aria-current={on ? "page" : undefined}
              className={`flex min-h-12 items-center gap-3 rounded-2xl px-4 text-[15px] font-medium transition-colors ${
                on ? "bg-gradient-to-b from-[#3b8cff] to-[#1f6fff] text-white shadow-[0_8px_24px_-8px_var(--glow)]" : "text-text/90 hover:bg-white/5"
              }`}
            >
              <I className="h-5 w-5" aria-hidden />
              <span className="flex-1">{it.label}</span>
              {it.dot && <span className="h-2.5 w-2.5 rounded-full bg-ok" aria-label="connected" />}
              {!!it.badge && <span className="grid h-6 min-w-6 place-items-center rounded-full bg-accent px-1.5 text-xs font-bold text-white">{it.badge}</span>}
            </Link>
          </li>
        );
      })}
    </ul>
  );
}
