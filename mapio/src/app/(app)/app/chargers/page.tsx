import type { Metadata } from "next";
import { getAppContext, getMapPageData } from "@/lib/app-context";
import { Paywall } from "@/components/app/paywall";
import { PageTitle } from "@/components/app/card";
import { ChargerBrowser } from "@/components/app/charger-browser";

export const metadata: Metadata = { title: "Chargers" };

export default async function ChargersPage() {
  const ctx = await getAppContext();
  if (!ctx.flags.chargers) return <PageTitle title="Chargers" sub="Charger discovery is temporarily disabled." />;
  if (!ctx.entitlement.hasAccess) return <Paywall billing={ctx.billing} feature="Charger discovery" />;
  const data = await getMapPageData(ctx.user.id);
  return (
    <>
      <PageTitle title="Chargers" sub="Public charging locations near you. Live stall availability is not available from our data sources." />
      <ChargerBrowser center={data.initialCenter} units={ctx.units} />
    </>
  );
}
