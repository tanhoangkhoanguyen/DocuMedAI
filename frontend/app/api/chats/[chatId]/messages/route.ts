import { proxyToBackend } from "@/lib/proxy-to-backend";

export async function GET(
  _request: Request,
  { params }: { params: Promise<{ chatId: string }> },
) {
  const { chatId } = await params;
  return proxyToBackend(
    `/chats/${encodeURIComponent(chatId)}/messages`,
  );
}

export async function POST(
  request: Request,
  { params }: { params: Promise<{ chatId: string }> },
) {
  const { chatId } = await params;
  const body = await request.json().catch(() => ({}));
  return proxyToBackend(
    `/chats/${encodeURIComponent(chatId)}/messages`,
    {
      method: "POST",
      body: JSON.stringify(body),
    },
  );
}
