import { NextResponse } from "next/server";
import { setAuthCookie } from "@/lib/auth-cookie";
import { getInternalApiBase } from "@/lib/utils/internal-api";

export async function POST(request: Request) {
  const body = await request.json().catch(() => ({}));
  const email = typeof body.email === "string" ? body.email : "";
  const password = typeof body.password === "string" ? body.password : "";
  const res = await fetch(`${getInternalApiBase()}/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
    cache: "no-store",
  });
  const text = await res.text();
  if (!res.ok) {
    return NextResponse.json({ error: text }, { status: res.status });
  }
  const data = JSON.parse(text) as { access_token?: string };                   // Parse JSON as object with optional access_token
  if (!data.access_token) {
    return NextResponse.json({ error: "No token" }, { status: 502 });
  }
  return setAuthCookie(NextResponse.json({ ok: true }), data.access_token);
}