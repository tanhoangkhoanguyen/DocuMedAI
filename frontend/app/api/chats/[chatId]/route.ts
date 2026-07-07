import { proxyToBackend } from "@/lib/proxy-to-backend";

export async function PATCH(
  request: Request,
  { params }: { params: Promise<{ chatId: string }> },
) {
  const { chatId } = await params;
  const body = await request.json().catch(() => ({}));
  return proxyToBackend(`/chats/${encodeURIComponent(chatId)}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
}

export async function DELETE(
  request: Request,
  { params }: { params: Promise<{ chatId: string }> },
) {
  const { chatId } = await params;
  return proxyToBackend(`/chats/${encodeURIComponent(chatId)}`, {
    method: "DELETE",
  });
}
