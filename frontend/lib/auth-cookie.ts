/*
Store the cookie in the browser.
For later requests, the browser automatically sends
```
GET /profile
Cookie: auth_token=abc123
```
*/
import { NextResponse } from "next/server";
import { LOCAL_COOKIE } from "@/lib/backend-bearer";

export const LOCAL_COOKIE_MAX_AGE = 60 * 60 * 24 * 7;                          // 1w

export function setAuthCookie(
  response: NextResponse,
  accessToken: string,
): NextResponse {
  response.cookies.set(LOCAL_COOKIE, accessToken, {
    httpOnly: true,
    sameSite: "lax",
    path: "/",
    maxAge: LOCAL_COOKIE_MAX_AGE,
    secure: process.env.NODE_ENV === "production",
  });
  return response;
}
