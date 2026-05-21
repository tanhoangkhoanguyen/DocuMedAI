import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import ChatLayout from "@/components/ChatLayout";
import { LOCAL_COOKIE } from "@/lib/backend-bearer";
import { getInternalApiBase } from "@/lib/internal-api";
import { createClient } from "@/lib/supabase/server";

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
        await fetch(`${getInternalApiBase()}/auth/supabase-sync`, {
          method: "POST",
          headers: { Authorization: `Bearer ${session.access_token}` },
          cache: "no-store",
        });
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
        const j = (await res.json()) as { display_name?: string };
        return <ChatLayout userEmail={j.display_name || "User"} />;
      }
    } catch {
      /* ignore */
    }
  }

  redirect("/login");
}
