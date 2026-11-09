import type { Metadata } from "next";
import { getAppContext, getMapPageData } from "@/lib/app-context";
import { Navigator } from "@/components/map/navigator";
import { Paywall } from "@/components/app/paywall";
import { sp, type SearchParams } from "@/lib/page";
import { parseDestination } from "@/lib/destination";

export const metadata: Metadata = { title: "Map" };

export default async function MapPage({ searchParams }: { searchParams: SearchParams }) {
  const ctx = await getAppContext();
  if (!ctx.entitlement.hasAccess) return <Paywall billing={ctx.billing} feature="The map" />;
  const q = await searchParams;
  const data = await getMapPageData(ctx.user.id);
  return (
    <div className="h-[calc(100dvh-10rem)] min-h-[520px] lg:h-[calc(100dvh-7.5rem)]">
      <Navigator
        variant="app"
        config={data.mapConfig}
        locale={ctx.profile.locale}
        units={ctx.units}
        t={ctx.t}
        initialCenter={data.initialCenter}
        devices={data.devices}
        flags={ctx.flags}
        initialDestination={parseDestination(q)}
        initialChargers={sp(q.chargers) === "1"}
      />
    </div>
  );
}
