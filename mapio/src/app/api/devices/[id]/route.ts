import { z } from "zod";
import { body, HttpError, json, parse, requireApiUser, route } from "@/lib/api";
import { renameDevice, revokeDevice } from "@/lib/repos/devices";
import { deviceName } from "@/lib/validation";

type Ctx = { params: Promise<{ id: string }> };
const id = z.string().uuid();

export const PATCH = route<Ctx>(async (req, ctx) => {
  const user = await requireApiUser();
  const devId = parse(id, (await ctx.params).id);
  const p = parse(z.object({ name: deviceName }), await body(req));
  const rows = await renameDevice(user.id, devId, p.name);
  if (!rows.length) throw new HttpError(404, "Device not found");
  return json({ ok: true });
});

export const DELETE = route<Ctx>(async (_req, ctx) => {
  const user = await requireApiUser();
  const devId = parse(id, (await ctx.params).id);
  const rows = await revokeDevice(user.id, devId);
  if (!rows.length) throw new HttpError(404, "Device not found");
  return json({ ok: true });
});
