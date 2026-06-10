import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import ChatLayout from "@/components/ChatLayout";
import { LOCAL_COOKIE } from "@/lib/backend-bearer";
import { getInternalApiBase } from "@/lib/internal-api";
import { syncSupabaseToBackend } from "@/lib/supabase-sync";
import { createClient } from "@/lib/utils/server_client";

export default async function Home() {
  const supabase = await createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (user) {
    const {
      data: { session },
    } = await supabase.auth.getSession();
    if (session?.access_token) {
      try {
        await syncSupabaseToBackend(session.access_token);
      } catch {
        /* backend may be down */
      }
    }
    return <ChatLayout userEmail={user.email ?? ""} />;
  }

  const jar = await cookies();
  const localTok = jar.get(LOCAL_COOKIE)?.value;
  if (localTok) {
    try {
      const res = await fetch(`${getInternalApiBase()}/auth/me`, {
        headers: { Authorization: `Bearer ${localTok}` },
        cache: "no-store",
      });
      if (res.ok) {
        const j = (await res.json()) as { username?: string };
        return <ChatLayout userEmail={j.username || "User"} />;
      }
    } catch {
      /* ignore */
    }
  }

  redirect("/login");
}