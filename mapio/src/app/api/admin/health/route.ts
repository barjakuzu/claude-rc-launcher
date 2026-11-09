import { HttpError, json, requireApiUser, route } from "@/lib/api";
import { isAdmin } from "@/lib/repos/admin";
import { providerHealth } from "@/lib/health";

export const GET = route(async () => {
  const user = await requireApiUser();
  if (!(await isAdmin(user.id))) throw new HttpError(403, "Forbidden");
  return json(await providerHealth());
});
