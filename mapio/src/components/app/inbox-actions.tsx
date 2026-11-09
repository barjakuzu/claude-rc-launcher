"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { Navigation2, X } from "lucide-react";

export function InboxActions({ id, lat, lon, name, dismissed }: { id: string; lat: number; lon: number; name: string; dismissed: boolean }) {
  const router = useRouter();
  return (
    <div className="flex gap-2">
      <Link
        href={`/app/map?lat=${lat}&lon=${lon}&name=${encodeURIComponent(name)}`}
        className="btn btn-ghost !min-h-11"
        onClick={() => void fetch(`/api/inbox/${id}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action: "open" }) })}
      >
        <Navigation2 className="h-4 w-4" aria-hidden /> Open
      </Link>
      {!dismissed && (
        <button
          className="btn btn-ghost !min-h-11 !px-3"
          aria-label={`Dismiss ${name}`}
          onClick={async () => {
            await fetch(`/api/inbox/${id}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action: "dismiss" }) });
            router.refresh();
          }}
        >
          <X className="h-4 w-4" />
        </button>
      )}
    </div>
  );
}
