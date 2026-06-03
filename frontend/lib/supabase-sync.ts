import { getInternalApiBase } from "@/lib/internal-api";

export async function syncSupabaseToBackend(accessToken: string): Promise<void> {
  await fetch(`${getInternalApiBase()}/auth/supabase-sync`, {
    method: "POST",
    headers: { Authorization: `Bearer ${accessToken}` },
    cache: "no-store",
  });
}
