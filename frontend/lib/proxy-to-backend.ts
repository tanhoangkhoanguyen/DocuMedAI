import { NextResponse } from "next/server";
import { getBackendBearer } from "@/lib/backend-bearer";
import { getInternalApiBase } from "@/lib/internal-api";

type ProxyInit = RequestInit & { requireAuth?: boolean };

export async function proxyToBackend(
  path: string,
  init: ProxyInit = {},
): Promise<NextResponse> {
  const { requireAuth = true, ...fetchInit } = init;
  let headers = new Headers(fetchInit.headers);
  if (requireAuth) {
    const token = await getBackendBearer();
    if (!token) {
      return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }
    headers.set("Authorization", `Bearer ${token}`);
  }
  if (!headers.has("Content-Type") && fetchInit.body) {
    headers.set("Content-Type", "application/json");
  }
  const res = await fetch(`${getInternalApiBase()}${path}`, {
    ...fetchInit,
    headers,
    cache: "no-store",
  });
  const text = await res.text();
  if (!res.ok) {
    return NextResponse.json({ error: text }, { status: res.status });
  }
  if (!text) {
    return NextResponse.json({});
  }
  try {
    return NextResponse.json(JSON.parse(text));
  } catch {
    return new NextResponse(text, { status: res.status });
  }
}