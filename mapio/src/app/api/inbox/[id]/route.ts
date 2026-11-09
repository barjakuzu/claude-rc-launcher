import { z } from "zod";
import { body, HttpError, json, parse, requireApiUser, route } from "@/lib/api";
import { actOnInbox } from "@/lib/repos/inbox";

type Ctx = { params: Promise<{ id: string }> };

export const POST = route<Ctx>(async (req, ctx) => {
  const user = await requireApiUser();
  const id = parse(z.string().uuid(), (await ctx.params).id);
  const p = parse(z.object({ action: z.enum(["open", "navigate", "dismiss"]) }), await body(req));
  if (!(await actOnInbox(user.id, id, p.action))) throw new HttpError(404, "Not found");
  return json({ ok: true });
});
