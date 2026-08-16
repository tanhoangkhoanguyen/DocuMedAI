import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import ChatLayout from "@/components/ChatLayout";
import { LOCAL_COOKIE } from "@/lib/backend-bearer";
import { getInternalApiBase } from "@/lib/utils/internal-api";

export default async function Home() {
  const authCookies = await cookies();
  const authUser = authCookies.get(LOCAL_COOKIE)?.value;
  if (authUser) {
    let username: string | null = null;
    try {
      const res = await fetch(`${getInternalApiBase()}/auth/me`, {
        headers: { Authorization: `Bearer ${authUser}` },
        cache: "no-store",
      });
      if (res.ok) {
        const metadata = (await res.json()) as { username?: string };
        username = metadata.username || "Unknown";
      }
    } catch {
        // backend is down
    }
    if (username !== null) return <ChatLayout userEmail={username} />;
  }

  redirect("/login");
}
