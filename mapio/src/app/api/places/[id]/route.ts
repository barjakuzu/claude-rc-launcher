import { z } from "zod";
import { body, HttpError, json, parse, requireApiUser, route } from "@/lib/api";
import { deleteSaved, renameSaved } from "@/lib/repos/places";

type Ctx = { params: Promise<{ id: string }> };

export const PATCH = route<Ctx>(async (req, ctx) => {
  const user = await requireApiUser();
  const id = parse(z.string().uuid(), (await ctx.params).id);
  const p = parse(z.object({ label: z.string().trim().min(1).max(60) }), await body(req));
  if (!(await renameSaved(user.id, id, p.label)).length) throw new HttpError(404, "Not found");
  return json({ ok: true });
});

export const DELETE = route<Ctx>(async (_req, ctx) => {
  const user = await requireApiUser();
  const id = parse(z.string().uuid(), (await ctx.params).id);
  if (!(await deleteSaved(user.id, id)).length) throw new HttpError(404, "Not found");
  return json({ ok: true });
});
