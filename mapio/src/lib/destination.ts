import { z } from "zod";
import { sp } from "@/lib/page";

const destSchema = z.object({
  lat: z.coerce.number().min(-90).max(90),
  lon: z.coerce.number().min(-180).max(180),
  name: z.string().trim().min(1).max(200).catch("Shared place"),
  address: z.string().max(300).catch(""),
});

/** Parses a destination handed in by URL (?lat&lon&name) — used by share links and the inbox. */
export function parseDestination(q: Record<string, string | string[] | undefined>) {
  if (sp(q.lat) == null || sp(q.lon) == null) return null;
  const r = destSchema.safeParse({ lat: sp(q.lat), lon: sp(q.lon), name: sp(q.name) ?? "Shared place", address: sp(q.address) ?? "" });
  return r.success ? r.data : null;
}
