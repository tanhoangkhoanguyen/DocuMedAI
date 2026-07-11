/*
Every incoming request goes through this 
middleware before reaching your pages/API routes.
*/
import { type NextRequest } from "next/server";
import { updateSession } from "@/lib/utils/middleware";

export async function middleware(request: NextRequest) {
  return await updateSession(request);
}

export const config = {
  matcher: [
    // Run middleware on all routes except,
    // - _next/static, _next/image
    // - favicon.ico
    // - image files
    "/((?!_next/static|_next/image|favicon.ico|.*\\.(?:svg|png|jpg|jpeg|gif|webp)$).*)",
  ],
};
