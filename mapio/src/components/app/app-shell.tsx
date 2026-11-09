import Link from "next/link";
import { Bell, Navigation, Search } from "lucide-react";
import { Logo } from "@/components/ui/logo";
import { BottomNavigation, NavLinks, type NavItem } from "./nav-links";
import { TopChips } from "./top-chips";
import { SearchShortcut } from "./search-shortcut";
import { signOutAction } from "@/app/(auth)/actions";
import { getAppContext, getUnread } from "@/lib/app-context";
import { listDevices, withPresence } from "@/lib/repos/devices";

export async function AppShell({ children }: { children: React.ReactNode }) {
  const ctx = await getAppContext();
  const [unread, devices] = await Promise.all([getUnread(ctx.user.id), listDevices(ctx.user.id)]);
  const { t } = ctx;
  const connected = withPresence(devices).some((d) => d.online);
  const items: NavItem[] = [
    { href: "/app", label: t["nav.dashboard"], icon: "LayoutGrid" },
    { href: "/app/map", label: t["nav.map"], icon: "Navigation" },
    { href: "/app/chargers", label: t["nav.chargers"], icon: "Zap" },
    { href: "/app/saved", label: t["nav.saved"], icon: "Star" },
    { href: "/app/devices", label: t["nav.devices"], icon: "Car", dot: connected },
    { href: "/app/inbox", label: t["nav.inbox"], short: "Inbox", icon: "Inbox", badge: unread || undefined },
    { href: "/account/billing", label: t["nav.billing"], icon: "CreditCard" },
    { href: "/account", label: t["nav.account"], icon: "UserRound" },
    ...(ctx.isAdmin ? [{ href: "/admin", label: t["nav.admin"], icon: "Shield" as const }] : []),
  ];
  const dock = [items[0], items[1], items[4], items[5], items[7]];
  const initials = (ctx.profile.display_name || ctx.user.email).slice(0, 1).toUpperCase();

  return (
    <div className="aurora min-h-dvh pt-[env(safe-area-inset-top)]" lang={ctx.profile.locale}>
      <a href="#main" className="btn btn-primary sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:z-50">
        Skip to content
      </a>
      <SearchShortcut />
      <header className="mx-auto flex max-w-[1680px] items-center gap-3 px-4 pt-4 sm:px-5">
        <Link href="/app" className="glass flex h-14 items-center !rounded-full px-5" aria-label="MAPIO dashboard">
          <Logo />
        </Link>
        <Link
          href="/app/map?focus=search"
          className="glass hidden h-14 min-w-0 flex-1 items-center gap-3 !rounded-full px-5 text-muted transition-colors hover:text-text lg:flex"
        >
          <Search className="h-5 w-5 shrink-0" aria-hidden />
          <span className="truncate">{t["search.placeholder"]}</span>
          <kbd className="ml-auto hidden rounded-lg border border-line px-2 py-0.5 font-sans text-xs text-faint xl:inline">⌘ K</kbd>
        </Link>
        <div className="ml-auto flex shrink-0 items-center gap-2 lg:ml-0">
          {ctx.entitlement.hasAccess && <TopChips flags={ctx.flags} units={ctx.units} currency={ctx.profile.currency} />}
          <Link
            href="/app/inbox"
            className="glass relative grid h-12 w-12 place-items-center !rounded-full"
            aria-label={`${t["nav.inbox"]}${unread ? ` (${unread} new)` : ""}`}
          >
            <Bell className="h-5 w-5" aria-hidden />
            {unread > 0 && <span className="absolute right-3 top-3 h-2.5 w-2.5 rounded-full bg-accent-2 shadow-[0_0_8px_var(--accent-2)]" />}
          </Link>
          <Link
            href="/account"
            className="grid h-12 w-12 place-items-center rounded-full border border-line-strong bg-gradient-to-br from-[#2d5aa6] to-[#0e1f40] text-lg font-semibold shadow-[inset_0_1px_0_rgba(255,255,255,0.2)]"
            aria-label={t["nav.account"]}
          >
            {initials}
          </Link>
        </div>
      </header>

      <div className="dock-clearance mx-auto grid max-w-[1680px] grid-cols-[minmax(0,1fr)] gap-5 px-4 pt-5 sm:px-5 lg:grid-cols-[228px_minmax(0,1fr)]">
        <aside className="hidden lg:block">
          <nav aria-label="App" className="glass sticky top-5 !rounded-[26px] p-2.5">
            <NavLinks items={items} />
            <div className="mt-3 border-t border-line px-1 pt-3">
              <Link href="/drive" className="btn btn-primary w-full">
                <Navigation className="h-5 w-5" aria-hidden /> {t["nav.drive"]}
              </Link>
              <form action={signOutAction} className="mt-1">
                <button className="w-full rounded-xl px-3 py-2 text-left text-sm text-muted transition-colors hover:text-text">{t["nav.signout"]}</button>
              </form>
            </div>
          </nav>
        </aside>
        <main id="main" className="min-w-0">
          {children}
        </main>
      </div>

      <BottomNavigation items={dock} />
    </div>
  );
}
