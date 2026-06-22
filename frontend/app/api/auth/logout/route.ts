import { NextResponse } from "next/server";
import { LOCAL_COOKIE } from "@/lib/backend-bearer";

export async function POST() {
  const out = NextResponse.json({ ok: true });          // Create a successful JSON response
  out.cookies.set(LOCAL_COOKIE, "", {                   // Clear the authentication cookie 
    httpOnly: true,
    sameSite: "lax",
    path: "/",
    maxAge: 0,
  });
  return out;
}
