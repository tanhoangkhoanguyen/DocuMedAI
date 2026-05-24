"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { createClient } from "@/lib/supabase/client";

export default function LoginPage() {
  const router = useRouter();
  const [magicEmail, setMagicEmail] = useState("");
  const [localUser, setLocalUser] = useState("");
  const [localPass, setLocalPass] = useState("");
  const [regUser, setRegUser] = useState("");
  const [regPass, setRegPass] = useState("");
  const [status, setStatus] = useState<string | null>(null);
  const [loading, setLoading] = useState<string | null>(null);

  async function sendMagicLink(e: React.FormEvent) {
    e.preventDefault();
    setLoading("magic");
    setStatus(null);
    const supabase = createClient();
    const origin = window.location.origin;
    const { error } = await supabase.auth.signInWithOtp({
      email: magicEmail.trim(),
      options: {
        emailRedirectTo: `${origin}/auth/callback`,
      },
    });
    setLoading(null);
    if (error) {
      setStatus(error.message);
      return;
    }
    setStatus("Check your email for the login link.");
  }

  async function passwordLogin(e: React.FormEvent) {
    e.preventDefault();
    setLoading("login");
    setStatus(null);
    const res = await fetch("/api/auth/local-login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        username: localUser.trim(),
        password: localPass,
      }),
    });
    setLoading(null);
    if (!res.ok) {
      const j = await res.json().catch(() => ({}));
      setStatus(typeof j.error === "string" ? j.error : "Login failed");
      return;
    }
    router.push("/");
    router.refresh();
  }

  async function register(e: React.FormEvent) {
    e.preventDefault();
    setLoading("register");
    setStatus(null);
    const res = await fetch("/api/auth/register", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        username: regUser.trim(),
        password: regPass,
      }),
    });
    setLoading(null);
    if (!res.ok) {
      const j = await res.json().catch(() => ({}));
      setStatus(typeof j.error === "string" ? j.error : "Could not create account");
      return;
    }
    router.push("/");
    router.refresh();
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-zinc-950 px-4 py-10">
      <div className="w-full max-w-md space-y-6">
        <div className="text-center">
          <h1 className="text-xl font-semibold text-zinc-50">DocuMedAI</h1>
          <p className="mt-1 text-sm text-zinc-400">Sign in or create an account</p>
        </div>

        <section className="rounded-xl border border-zinc-800 bg-zinc-900/50 p-6 space-y-4">
          <h2 className="text-sm font-medium text-zinc-200">1. Username + password</h2>
          <form onSubmit={passwordLogin} className="space-y-3">
            <input
              type="text"
              required
              autoComplete="username"
              value={localUser}
              onChange={(e) => setLocalUser(e.target.value)}
              placeholder="Username"
              className="w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm text-zinc-100 outline-none focus:border-zinc-500"
            />
            <input
              type="password"
              required
              autoComplete="current-password"
              value={localPass}
              onChange={(e) => setLocalPass(e.target.value)}
              placeholder="Password"
              className="w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm text-zinc-100 outline-none focus:border-zinc-500"
            />
            <button
              type="submit"
              disabled={loading !== null}
              className="w-full rounded-lg bg-zinc-100 py-2 text-sm font-medium text-zinc-900 hover:bg-white disabled:opacity-50"
            >
              {loading === "login" ? "Signing in…" : "Log in"}
            </button>
          </form>
        </section>

        <section className="rounded-xl border border-zinc-800 bg-zinc-900/50 p-6 space-y-4">
          <h2 className="text-sm font-medium text-zinc-200">2. Magic link (email)</h2>
          <form onSubmit={sendMagicLink} className="space-y-3">
            <input
              type="email"
              required
              autoComplete="email"
              value={magicEmail}
              onChange={(e) => setMagicEmail(e.target.value)}
              placeholder="you@gmail.com"
              className="w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm text-zinc-100 outline-none focus:border-zinc-500"
            />
            <button
              type="submit"
              disabled={loading !== null}
              className="w-full rounded-lg border border-zinc-600 bg-zinc-800 py-2 text-sm text-zinc-100 hover:bg-zinc-700 disabled:opacity-50"
            >
              {loading === "magic" ? "Sending…" : "Send magic link"}
            </button>
          </form>
        </section>

        <section className="rounded-xl border border-zinc-800 bg-zinc-900/50 p-6 space-y-4">
          <h2 className="text-sm font-medium text-zinc-200">3. Create account (new users)</h2>
          <p className="text-xs text-zinc-500">
            Required if you do not already have a username in the app database.
          </p>
          <form onSubmit={register} className="space-y-3">
            <input
              type="text"
              required
              minLength={2}
              autoComplete="username"
              value={regUser}
              onChange={(e) => setRegUser(e.target.value)}
              placeholder="Choose username"
              className="w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm text-zinc-100 outline-none focus:border-zinc-500"
            />
            <input
              type="password"
              required
              minLength={6}
              autoComplete="new-password"
              value={regPass}
              onChange={(e) => setRegPass(e.target.value)}
              placeholder="Password (min 6 characters)"
              className="w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm text-zinc-100 outline-none focus:border-zinc-500"
            />
            <button
              type="submit"
              disabled={loading !== null}
              className="w-full rounded-lg bg-emerald-700 py-2 text-sm font-medium text-white hover:bg-emerald-600 disabled:opacity-50"
            >
              {loading === "register" ? "Creating…" : "Create account & sign in"}
            </button>
          </form>
        </section>

        {status ? (
          <p className="text-center text-sm text-zinc-400" role="status">
            {status}
          </p>
        ) : null}
      </div>
    </div>
  );
}
