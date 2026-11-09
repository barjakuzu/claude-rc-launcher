import { withService } from "@/lib/db";

export async function createUser(email: string): Promise<string> {
  const [r] = await withService((tx) =>
    tx.query<{ id: string }>(`insert into auth.users (email) values ($1) returning id`, [email]),
  );
  return r.id;
}
