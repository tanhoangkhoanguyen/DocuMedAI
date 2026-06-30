import { NextResponse } from "next/server";
import { getBackendBearer } from "@/lib/backend-bearer";
import { getInternalApiBase } from "@/lib/internal-api";

// Stream-through proxy: pipes the backend SSE body straight to the client
// without buffering (unlike proxyToBackend, which awaits res.text()).
export async function POST(
  request: Request,
  { params }: { params: Promise<{ chatId: string }> },
) {
  const { chatId } = await params;
  const body = await request.json().catch(() => ({}));

  const token = await getBackendBearer();
  if (!token) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  const res = await fetch(
    `${getInternalApiBase()}/chats/${encodeURIComponent(chatId)}/messages/stream`,
    {
      method: "POST",
      headers: {
        Authorization: `Bearer ${token}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify(body),
      cache: "no-store",
    },
  );

  if (!res.ok || !res.body) {
    const text = await res.text().catch(() => "");
    return NextResponse.json(
      { error: text || "Upstream stream failed" },
      { status: res.status || 502 },
    );
  }

  return new NextResponse(res.body, {
    status: 200,
    headers: {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache, no-transform",
      Connection: "keep-alive",
    },
  });
}
