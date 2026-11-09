import type { Metadata } from "next";
import { Info } from "lucide-react";
import { getAppContext, getMapPageData } from "@/lib/app-context";
import { listDevices, withPresence } from "@/lib/repos/devices";
import { Paywall } from "@/components/app/paywall";
import { Card, PageTitle } from "@/components/app/card";
import { DeviceList, PairThisBrowser } from "@/components/app/devices-manager";

export const metadata: Metadata = { title: "Devices" };

export default async function DevicesPage() {
  const ctx = await getAppContext();
  if (!ctx.entitlement.hasAccess) return <Paywall billing={ctx.billing} feature="Send to Car" />;
  const [devices, map] = await Promise.all([listDevices(ctx.user.id), getMapPageData(ctx.user.id)]);
  return (
    <>
      <PageTitle title={ctx.t["nav.devices"]} sub="Pair your car's browser to receive destinations sent from your phone." />
      <div className="grid gap-5">
        <Card title="Pair this browser">
          <PairThisBrowser alreadyPaired={!!map.currentDeviceId} />
        </Card>
        <Card title="Paired browsers">
          <DeviceList devices={withPresence(devices)} currentId={map.currentDeviceId} />
        </Card>
        <p className="flex items-start gap-2 text-sm text-muted">
          <Info className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
          A paired device is a browser session signed in to your account — not a connection to your vehicle. Pairing codes
          expire after 5 minutes and work once. Revoking a device stops delivery immediately.
        </p>
      </div>
    </>
  );
}
