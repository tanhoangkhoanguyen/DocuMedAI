import { NextResponse } from "next/server";
import { LOCAL_COOKIE } from "@/lib/backend-bearer";

export async function POST() {
  const out = NextResponse.json({ ok: true });
  out.cookies.set(LOCAL_COOKIE, "", {
    httpOnly: true,
    sameSite: "lax",
    path: "/",
    maxAge: 0,
  });
  return out;
}
