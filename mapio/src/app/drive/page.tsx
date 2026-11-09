import type { Metadata, Viewport } from "next";
import Link from "next/link";
import { getAppContext, getMapPageData } from "@/lib/app-context";
import { Navigator } from "@/components/map/navigator";
import { Paywall } from "@/components/app/paywall";
import { parseDestination } from "@/lib/destination";
import type { SearchParams } from "@/lib/page";

export const metadata: Metadata = { title: "Drive", robots: { index: false, follow: false } };
export const viewport: Viewport = { themeColor: "#040a17", initialScale: 1, maximumScale: 1, userScalable: false };

/**
 * Driving UI for landscape in-car browsers: the map fills the screen; only search, the
 * turn card, the ETA bar and one compact toolbar overlay it. No account/billing panels.
 */
export default async function DrivePage({ searchParams }: { searchParams: SearchParams }) {
  const ctx = await getAppContext();
  if (!ctx.entitlement.hasAccess) {
    return (
      <div className="aurora grid min-h-dvh place-items-center p-4">
        <div className="w-full">
          <Paywall billing={ctx.billing} feature="Drive mode" />
          <p className="mt-4 text-center"><Link href="/app" className="text-accent-2 underline">Back to dashboard</Link></p>
        </div>
      </div>
    );
  }
  const data = await getMapPageData(ctx.user.id);
  return (
    <main className="fixed inset-0 bg-bg" lang={ctx.profile.locale}>
      <Navigator
        variant="drive"
        config={data.mapConfig}
        locale={ctx.profile.locale}
        units={ctx.units}
        t={ctx.t}
        initialCenter={data.initialCenter}
        devices={data.devices}
        flags={ctx.flags}
        initialDestination={parseDestination(await searchParams)}
      />
    </main>
  );
}
