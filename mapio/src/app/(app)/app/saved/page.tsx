import type { Metadata } from "next";
import { getAppContext } from "@/lib/app-context";
import { listSaved } from "@/lib/repos/places";
import { Paywall } from "@/components/app/paywall";
import { PageTitle } from "@/components/app/card";
import { SavedManager } from "@/components/app/saved-manager";

export const metadata: Metadata = { title: "Saved places" };

export default async function SavedPage() {
  const ctx = await getAppContext();
  if (!ctx.entitlement.hasAccess) return <Paywall billing={ctx.billing} feature="Saved places" />;
  const saved = await listSaved(ctx.user.id);
  return (
    <>
      <PageTitle title={ctx.t["nav.saved"]} sub="Home, Work and favourites — synced across your browsers." />
      <SavedManager initial={saved.map((s) => ({ ...s, lat: Number(s.lat), lon: Number(s.lon) }))} />
    </>
  );
}
