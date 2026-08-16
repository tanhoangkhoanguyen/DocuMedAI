import { cookies } from "next/headers";

const LOCAL_COOKIE = "documedai";

export async function getBackendBearer(): Promise<string | null> {
  const jar = await cookies();
  return jar.get(LOCAL_COOKIE)?.value ?? null;
}

export { LOCAL_COOKIE };
