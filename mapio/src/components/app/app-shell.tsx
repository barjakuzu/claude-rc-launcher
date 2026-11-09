import Link from "next/link";
import { Bell, Navigation, Search } from "lucide-react";
import { Logo } from "@/components/ui/logo";
import { NavLinks, type NavItem } from "./nav-links";
import { TopChips } from "./top-chips";
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
    { href: "/app/inbox", label: t["nav.inbox"], icon: "Inbox", badge: unread || undefined },
    { href: "/account/billing", label: t["nav.billing"], icon: "CreditCard" },
    { href: "/account", label: t["nav.account"], icon: "UserRound" },
    ...(ctx.isAdmin ? [{ href: "/admin", label: t["nav.admin"], icon: "Shield" as const }] : []),
  ];
  const initials = (ctx.profile.display_name || ctx.user.email).slice(0, 1).toUpperCase();

  return (
    <div className="aurora min-h-dvh" lang={ctx.profile.locale}>
      <a href="#main" className="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:z-50 btn btn-primary">Skip to content</a>
      <header className="mx-auto flex max-w-[1600px] items-center gap-3 px-4 pt-4">
        <Link href="/app" className="glass !rounded-full px-5 py-2.5" aria-label="MAPIO dashboard">
          <Logo />
        </Link>
        <Link href="/app/map" className="glass hidden min-h-12 flex-1 items-center gap-3 !rounded-full px-5 text-muted md:flex">
          <Search className="h-5 w-5" aria-hidden /> {t["search.placeholder"]}
        </Link>
        <div className="ml-auto flex items-center gap-2 md:ml-0">
          {ctx.entitlement.hasAccess && <TopChips flags={ctx.flags} units={ctx.units} currency={ctx.profile.currency} />}
          <Link href="/app/inbox" className="glass relative grid h-12 w-12 place-items-center !rounded-full" aria-label={`${t["nav.inbox"]}${unread ? ` (${unread})` : ""}`}>
            <Bell className="h-5 w-5" aria-hidden />
            {unread > 0 && <span className="absolute right-2.5 top-2.5 h-2.5 w-2.5 rounded-full bg-accent-2" />}
          </Link>
          <Link href="/account" className="grid h-12 w-12 place-items-center rounded-full border-2 border-line-strong bg-gradient-to-br from-[#2a4a86] to-[#0e1f40] text-lg font-bold" aria-label={t["nav.account"]}>
            {initials}
          </Link>
        </div>
      </header>

      <div className="mx-auto grid max-w-[1600px] gap-5 px-4 pb-28 pt-5 lg:grid-cols-[250px_1fr] lg:pb-8">
        <aside className="hidden lg:block">
          <nav aria-label="App" className="glass sticky top-5 p-3">
            <NavLinks items={items} variant="side" />
            <div className="mt-4 border-t border-line pt-4">
              <Link href="/drive" className="btn btn-primary w-full">
                <Navigation className="h-5 w-5" aria-hidden /> {t["nav.drive"]}
              </Link>
              <form action={signOutAction} className="mt-2">
                <button className="w-full rounded-xl px-4 py-2 text-left text-sm text-muted hover:text-text">{t["nav.signout"]}</button>
              </form>
            </div>
          </nav>
        </aside>
        <main id="main" className="min-w-0">{children}</main>
      </div>

      <nav aria-label="App" className="glass-strong fixed inset-x-2 bottom-2 z-40 !rounded-2xl lg:hidden">
        <NavLinks items={[items[0], items[1], items[4], items[5], items[7]]} variant="bottom" />
      </nav>
    </div>
  );
}
