/** Base URL for the FastAPI service (server-side only). */
export function getInternalApiBase(): string {
  const base = process.env.INTERNAL_API_BASE;
  if (!base) {
    throw new Error("INTERNAL_API_BASE is not set");
  }
  return base.replace(/\/$/, "");
}