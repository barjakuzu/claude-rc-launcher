import { json, limit, requireApiUser, route } from "@/lib/api";
import { exportUserData } from "@/lib/repos/profiles";

export const GET = route(async () => {
  const user = await requireApiUser();
  limit(`export:${user.id}`, 5, 60 * 60_000);
  const data = await exportUserData(user.id);
  const res = json({ email: user.email, ...data });
  res.headers.set("Content-Disposition", `attachment; filename="mapio-export-${new Date().toISOString().slice(0, 10)}.json"`);
  return res;
});
