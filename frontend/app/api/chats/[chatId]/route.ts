import { NextResponse } from "next/server";
import { getBackendBearer } from "@/lib/backend-bearer";
import { getInternalApiBase } from "@/lib/internal-api";

export async function PATCH(
  request: Request,
  { params }: { params: Promise<{ chatId: string }> },
) {
  const { chatId } = await params;
  const token = await getBackendBearer();
  if (!token) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }
  const body = await request.json().catch(() => ({}));
  const res = await fetch(`${getInternalApiBase()}/chats/${encodeURIComponent(chatId)}`, {
    method: "PATCH",
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body),
    cache: "no-store",
  });
  const text = await res.text();
  if (!res.ok) {
    return NextResponse.json({ error: text }, { status: res.status });
  }
  return NextResponse.json(JSON.parse(text));
}
