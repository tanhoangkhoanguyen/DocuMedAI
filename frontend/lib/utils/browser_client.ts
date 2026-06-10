import { createBrowserClient } from "@supabase/ssr";
import { getSupabaseUrl, getSupabaseAnonKey } from "@/lib/utils/env";

export function createClient() {
  return createBrowserClient(getSupabaseUrl(), getSupabaseAnonKey());
}