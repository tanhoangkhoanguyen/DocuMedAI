// Next.js lets you choose where a component runs (server/broswer)
"use client";                                                       // Tell Next.js to run this component in the browser


import { useState } from "react";
import { useRouter } from "next/navigation";                        // Navigate between pages

import BrandPanel from "@/components/utils/BrandPanel";
import Field from "@/components/utils/Field";


type Mode = "signin" | "register";                                   // Literal Union Strings


export default function LoginPage() {
  const router = useRouter();

  const [mode, setMode] = useState<Mode>("signin");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");

  const [status, setStatus] = useState<{ kind: "error" | "ok"; text: string } | null>(null);
  const [loading, setLoading] = useState(false);

  // When a form is submitted, React passes information (e) into the function
  async function handleAuth(e: React.FormEvent) {
    e.preventDefault();                                             // Prevent page refresh
    setLoading(true);                                               // Update loading variable
    setStatus(null);

    const endpoint = mode === "signin" ? "/api/auth/login" : "/api/auth/register";
    const res = await fetch(endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email: email.trim(), password }),
    });

    setLoading(false);
    if (!res.ok) {
      const j = await res.json().catch(() => ({}));
      setStatus({
        kind: "error",
        text:
          typeof j.error === "string"
            ? j.error
            : mode === "signin"
              ? "Login failed"
              : "Could not create account",
      });
      return;
    }
    router.push("/");
    router.refresh();
  }

  return (
    <main
        className="grid min-h-screen grid-cols-1
                    bg-[#F2EEE4] text-[#102A26]
                    lg:grid-cols-[1.05fr_0.95fr]
        "
      >
      {/* ── Brand panel ─────────────────────────────────────────────── */}
      <BrandPanel />

      {/* ── Form panel ──────────────────────────────────────────────── */}
      <section className="relative flex items-center justify-center px-6 py-12 sm:px-10">
        <div className="absolute right-8 top-8 hidden font-mono text-[10.5px] uppercase tracking-[0.24em] text-[#102A26]/40 sm:block">
          {mode === "signin" ? "Member access" : "New record"}
        </div>

        <div className="w-full max-w-[400px]">
          {/* key changes on every mode switch, so this block re-mounts and re-runs fadeIn */}
          <div key={mode} style={{ animation: "fadeIn 1s ease-out" }}>
            <h2
              className="text-[2rem] font-normal leading-tight tracking-[-0.01em]"
              style={{ fontFamily: "var(--font-display)" }}
            >
              {mode === "signin" ? "Welcome back." : "Create your account."}
            </h2>
            <p className="mt-2 text-[14px] text-[#102A26]/55">
              {mode === "signin"
                ? "Enter your credentials to continue."
                : "Set up access to the document workspace."}
            </p>
          </div>

          {/* Segmented toggle */}
          <div className="mt-8">
            <div className="relative grid grid-cols-2 rounded-full border border-[#102A26]/12 bg-[#102A26]/[0.03] p-1 font-mono text-[11px] uppercase tracking-[0.16em]">
              {/* Sliding pill: sits behind the labels and slides to the active side */}
              <span
                aria-hidden
                className="absolute inset-y-1 left-1 w-[calc(50%-4px)] rounded-full bg-[#0E221F] transition-transform duration-1000 ease-out"
                style={{
                  transform: mode === "register" ? "translateX(100%)" : "translateX(0)",
                }}
              />
              {(["signin", "register"] as Mode[]).map((m) => (
                <button
                  key={m}
                  type="button"
                  onClick={() => {
                    setMode(m);
                    setStatus(null);
                  }}
                  className={`relative z-10 rounded-full py-2 transition-colors duration-300 ${
                    mode === m
                      ? "text-[#E9E4D6]"
                      : "text-[#102A26]/50 hover:text-[#102A26]/80"
                  }`}
                >
                  {m === "signin" ? "Sign in" : "Register"}
                </button>
              ))}
            </div>
          </div>

          <form onSubmit={handleAuth} className="mt-7">
            <Field
              label="Email"
              type="email"
              required
              autoComplete="email"
              placeholder="name@clinic.org"
              value={email}
              onChange={setEmail}
            />

            <div className="mt-5">
              <Field
                label="Password"
                type="password"
                required
                minLength={mode === "register" ? 6 : undefined}
                autoComplete={
                  mode === "signin" ? "current-password" : "new-password"
                }
                placeholder={
                  mode === "register" ? "Minimum 6 characters" : "••••••••"
                }
                value={password}
                onChange={setPassword}
              />
            </div>

            <button
              type="submit"
              disabled={loading}
              className="group relative mt-5 flex w-full items-center justify-center gap-2 overflow-hidden rounded-xl bg-[#FF5436] py-3.5 text-[14px] font-medium text-[#1A0A06] transition-[transform,background] hover:bg-[#ff6a51] active:translate-y-px disabled:cursor-not-allowed disabled:opacity-60"
            >
              {/* key changes with the label, so the text crossfades on switch */}
              <span
                key={loading ? "working" : mode}
                style={{ animation: "fadeIn 1s ease-out" }}
              >
                {loading
                  ? "Working…"
                  : mode === "signin"
                    ? "Sign in"
                    : "Create account & continue"}
              </span>
              <span
                aria-hidden
                className="transition-transform duration-300 group-hover:translate-x-1"
              >
                →
              </span>
            </button>
          </form>

          {status && (
            <p
              role="status"
              className={`mt-6 flex items-start gap-2 text-[13px] leading-relaxed ${
                status.kind === "error" ? "text-[#C23A24]" : "text-[#1F6F5C]"
              }`}
            >
              <span aria-hidden className="mt-px font-mono">
                {status.kind === "error" ? "✕" : "✓"}
              </span>
              {status.text}
            </p>
          )}
        </div>
      </section>
    </main>
  );
}
