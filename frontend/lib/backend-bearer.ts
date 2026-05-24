import { cookies } from "next/headers";
import { createClient } from "@/lib/supabase/server";

const LOCAL_COOKIE = "documedai_local";

/** Bearer token for FastAPI: Supabase session first, else local JWT cookie. */
export async function getBackendBearer(): Promise<string | null> {
  const supabase = await createClient();
  const {
    data: { session },
  } = await supabase.auth.getSession();
  if (session?.access_token) {
    return session.access_token;
  }
  const jar = await cookies();
  return jar.get(LOCAL_COOKIE)?.value ?? null;
}

export { LOCAL_COOKIE };
