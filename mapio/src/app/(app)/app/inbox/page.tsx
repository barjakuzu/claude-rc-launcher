import type { Metadata } from "next";
import Link from "next/link";
import { MapPin, Send } from "lucide-react";
import { getAppContext } from "@/lib/app-context";
import { listAllInbox } from "@/lib/repos/inbox";
import { Paywall } from "@/components/app/paywall";
import { Card, PageTitle } from "@/components/app/card";
import { InboxActions } from "@/components/app/inbox-actions";
import { relativeTime } from "@/lib/format";

export const metadata: Metadata = { title: "Send to Car inbox" };

const STATE_LABEL: Record<string, string> = {
  sent: "Waiting for car browser",
  received: "Delivered",
  opened: "Opened",
  navigating: "Navigating",
  dismissed: "Dismissed",
};

export default async function InboxPage() {
  const ctx = await getAppContext();
  if (!ctx.entitlement.hasAccess) return <Paywall billing={ctx.billing} feature="Send to Car" />;
  const items = await listAllInbox(ctx.user.id, 50);
  return (
    <>
      <PageTitle
        title={ctx.t["nav.inbox"]}
        sub="Destinations sent to your paired browsers. Delivery status updates when the car browser picks them up."
        action={<Link href="/app/map" className="btn btn-primary"><Send className="h-4 w-4" aria-hidden /> Send a destination</Link>}
      />
      <Card>
        {items.length === 0 ? (
          <p className="text-muted">
            Nothing sent yet. <Link href="/app/devices" className="font-semibold text-accent-2 underline">Pair a car browser</Link>, then use “Send to Car” on any place.
          </p>
        ) : (
          <ul className="divide-y divide-line">
            {items.map((i) => (
              <li key={i.id} className="flex flex-wrap items-center gap-3 py-3">
                <MapPin className="h-5 w-5 text-accent-2" aria-hidden />
                <div className="min-w-0 flex-1">
                  <p className="truncate font-semibold">{i.name}</p>
                  <p className="text-sm text-muted">
                    to {i.device_name} · sent {relativeTime(i.sent_at)} · <span className={i.state === "sent" ? "text-warn" : ""}>{STATE_LABEL[i.state] ?? i.state}</span>
                  </p>
                </div>
                <InboxActions id={i.id} lat={Number(i.lat)} lon={Number(i.lon)} name={i.name} dismissed={i.state === "dismissed"} />
              </li>
            ))}
          </ul>
        )}
      </Card>
    </>
  );
}
