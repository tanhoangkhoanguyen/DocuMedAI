import { proxyToBackend } from "@/lib/proxy-to-backend";

export async function GET() {
  return proxyToBackend("/chats");
}

export async function POST(request: Request) {
  const body = await request.json().catch(() => ({}));
  return proxyToBackend("/chats", {
    method: "POST",
    body: JSON.stringify(body),
  });
}
