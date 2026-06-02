import { NextResponse } from "next/server";
import { LOCAL_COOKIE } from "@/lib/backend-bearer";
import { getInternalApiBase } from "@/lib/internal-api";

const COOKIE_MAX_AGE = 60 * 60 * 24 * 7;

export async function POST(request: Request) {
  const body = await request.json().catch(() => ({}));
  const email = typeof body.email === "string" ? body.email : "";
  const password = typeof body.password === "string" ? body.password : "";
  const res = await fetch(`${getInternalApiBase()}/auth/register`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
    cache: "no-store",
  });
  const text = await res.text();
  if (!res.ok) {
    return NextResponse.json({ error: text }, { status: res.status });
  }
  const data = JSON.parse(text) as { access_token?: string };
  if (!data.access_token) {
    return NextResponse.json({ error: "No token" }, { status: 502 });
  }
  const out = NextResponse.json({ ok: true });
  out.cookies.set(LOCAL_COOKIE, data.access_token, {
    httpOnly: true,
    sameSite: "lax",
    path: "/",
    maxAge: COOKIE_MAX_AGE,
    secure: process.env.NODE_ENV === "production",
  });
  return out;
}
