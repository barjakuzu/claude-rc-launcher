import { z } from "zod";

export const lat = z.coerce.number().min(-90).max(90);
export const lon = z.coerce.number().min(-180).max(180);
export const lang = z.enum(["en", "ka"]).catch("en");

export const placeInput = z.object({
  name: z.string().trim().min(1).max(200),
  address: z.string().trim().max(300).optional().default(""),
  lat,
  lon,
  source: z.string().max(40).optional(),
  sourceRef: z.string().max(200).optional(),
});

export const savedPlaceInput = placeInput.extend({
  kind: z.enum(["home", "work", "favorite"]).default("favorite"),
  label: z.string().trim().min(1).max(60),
});

export const sendToCarInput = placeInput.extend({
  deviceId: z.string().uuid(),
  note: z.string().trim().max(200).optional(),
});

export const deviceName = z.string().trim().min(1).max(60);

export const profileInput = z.object({
  display_name: z.string().trim().max(80).optional(),
  locale: z.enum(["en", "ka"]),
  distance_unit: z.enum(["km", "mi"]),
  temperature_unit: z.enum(["c", "f"]),
  time_format: z.enum(["24h", "12h"]),
  currency: z.string().regex(/^[A-Z]{3}$/),
  theme: z.enum(["dark", "light", "system"]),
  notify_inbox: z.coerce.boolean(),
  keep_recent_destinations: z.coerce.boolean(),
});

export const credentials = z.object({
  email: z.string().trim().email().max(254),
  password: z.string().min(8, "Use at least 8 characters.").max(128),
});
