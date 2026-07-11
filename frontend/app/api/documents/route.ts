import { NextResponse } from "next/server";
import { getBackendBearer } from "@/lib/backend-bearer";
import { getInternalApiBase } from "@/lib/utils/internal-api";
import { proxyToBackend } from "@/lib/proxy-to-backend";

// The caller's current document + ingestion status (or 404). JSON GET is safe
// through the shared proxy.
export async function GET() {
  return proxyToBackend("/documents");
}

// Multipart upload. Must NOT go through proxyToBackend — that forces
// Content-Type: application/json and would clobber the multipart boundary.
// Forward the native FormData and let fetch set the boundary itself.
export async function POST(request: Request) {
  const token = await getBackendBearer();
  if (!token) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  const form = await request.formData();

  const res = await fetch(`${getInternalApiBase()}/documents`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` }, // no Content-Type — fetch adds the boundary
    body: form,
    cache: "no-store",
  });

  const text = await res.text();
  if (!res.ok) {
    return NextResponse.json({ error: text || "Upload failed" }, { status: res.status });
  }
  try {
    return NextResponse.json(JSON.parse(text));
  } catch {
    return new NextResponse(text, { status: res.status });
  }
}
