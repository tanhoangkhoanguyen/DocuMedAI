import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import ChatLayout from "@/components/ChatLayout";
import { LOCAL_COOKIE } from "@/lib/backend-bearer";
import { getInternalApiBase } from "@/lib/utils/internal-api";
import { syncSupabaseToBackend } from "@/lib/supabase-sync";
import { createClient } from "@/lib/utils/server_client";

export default async function Home() {
  const supabase = await createClient();
  const {
    data: { SupabaseUser },
  } = await supabase.auth.getUser();
  if (SupabaseUser) {
    const {
      data: { session },
    } = await supabase.auth.getSession();
    if (session?.access_token) {
      try {
        await syncSupabaseToBackend(session.access_token);
      } catch {
        // backend is down
      }
    }
    return <ChatLayout userEmail={SupabaseUser.email ?? ""} />;
  }

  const authCookies = await cookies();
  const authUser = authCookies.get(LOCAL_COOKIE)?.value;
  if (authUser) {
    try {
      const res = await fetch(`${getInternalApiBase()}/auth/me`, {
        headers: { Authorization: `Bearer ${authUser}` },
        cache: "no-store",
      });
      if (res.ok) {
        const metadata = (await res.json()) as { username?: string };
        return <ChatLayout userEmail={metadata.username || "Unknown"} />;
      }
    } catch {
        // backend is down
    }
  }

  redirect("/login");
}