// Next.js lets you choose where a component runs (server/broswer)
"use client";                                                       // Tell Next.js to run this component in the browser


import { useEffect, useId, useRef, useState } from "react";
import { useRouter } from "next/navigation";                        // Navigate between pages
import { Fraunces, Spline_Sans, Spline_Sans_Mono } from "next/font/google";

import { createClient } from "@/lib/utils/browser_client";


// TODO: Config fonts in shared file
const display = Fraunces({
  subsets: ["latin"],
  weight: ["400", "500", "600"],
  style: ["normal", "italic"],
  variable: "--font-display",
});
const sans = Spline_Sans({
  subsets: ["latin"],
  weight: ["300", "400", "500", "600"],
  variable: "--font-sans",
});
const mono = Spline_Sans_Mono({
  subsets: ["latin"],
  weight: ["400", "500"],
  variable: "--font-mono",
});


type Mode = "signin" | "register";                                 // Literal Union Strings


export default function LoginPage() {
  const router = useRouter();

  const [mode, setMode] = useState<Mode>("signin");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [magicMode, setMagicMode] = useState(false);

  const [status, setStatus] = useState<{ kind: "error" | "ok"; text: string } | null>(null);
  const [loading, setLoading] = useState<null | "auth" | "magic">(null);

  // When a form is submitted, React passes information (e) into the function
  async function handleAuth(e: React.FormEvent) {
    e.preventDefault();                                             // Prevent page refresh
    setLoading("auth");                                             // Update loading variable
    setStatus(null);

    const endpoint = mode === "signin" ? "/api/auth/login" : "/api/auth/register";
    const res = await fetch(endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email: email.trim(), password }),
    });

    setLoading(null);
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

  async function sendMagicLink(e: React.FormEvent) {
    e.preventDefault();
    setLoading("magic");
    setStatus(null);

    const supabase = createClient();
    const origin = window.location.origin;                        // Get current url
    const { error } = await supabase.auth.signInWithOtp({         // Send magic link to email
      email: email.trim(),
      options: { emailRedirectTo: `${origin}/auth/callback` },
    });

    setLoading(null);
    if (error) {
      setStatus({ kind: "error", text: error.message });
      return;
    }
    setStatus({ kind: "ok", text: "A sign-in link is on its way to your inbox." });
  }

  return (
    <main
        className={`${display.variable} ${sans.variable} ${mono.variable} 
                    grid min-h-screen grid-cols-1 
                    bg-[#F2EEE4] text-[#102A26] 
                    lg:grid-cols-[1.05fr_0.95fr]
        `}
        style={{ fontFamily: "var(--font-sans)" }}
      >
      {/* ── Brand panel ─────────────────────────────────────────────── */}
      <section className="relative hidden flex-col justify-between overflow-hidden bg-[#0E221F] p-12 text-[#E9E4D6] lg:flex">
        <div className="pointer-events-none absolute inset-x-0 top-1/2 -translate-y-1/2">
          <PulseLine />
        </div>

        <header className="relative flex items-center gap-3">
          <Glyph />
          <span className="font-mono text-[11px] uppercase tracking-[0.32em] text-[#8FB3A6]">
            DocuMedAI
          </span>
        </header>

        <div className="relative max-w-md">
          <p className="mb-3 font-mono text-[11px] uppercase tracking-[0.3em] text-[#FF5436]">
            Clinical AI Assistant
          </p>
          <h1
            className="text-balance text-[2.9rem] font-light leading-[1.05] tracking-[-0.02em]"
            style={{ fontFamily: "var(--font-display)" }}
          >
            Ask anything.
            <br />
            <span className="italic text-[#9FC4B6]">Get fast, grounded answers.</span>
          </h1>
        </div>

        <footer className="relative flex items-center justify-between font-mono text-[10.5px] uppercase tracking-[0.22em] text-[#6E8A82]">
          <span>tanhoangkhoanguyen</span>
          <span aria-hidden className="h-px w-10 bg-[#2C453F]" />
          <span>End-to-end encrypted</span>
        </footer>
      </section>

      {/* ── Form panel ──────────────────────────────────────────────── */}
      <section className="relative flex items-center justify-center px-6 py-12 sm:px-10">
        <div className="absolute right-8 top-8 hidden font-mono text-[10.5px] uppercase tracking-[0.24em] text-[#102A26]/40 sm:block">
          {mode === "signin" ? "Member access" : "New record"}
        </div>

        <div className="w-full max-w-[400px]">
          {/* key changes on every mode switch, so this block re-mounts and re-runs fadeIn */}
          <div key={magicMode ? "magic" : mode} style={{ animation: "fadeIn 1s ease-out" }}>
            <h2
              className="text-[2rem] font-normal leading-tight tracking-[-0.01em]"
              style={{ fontFamily: "var(--font-display)" }}
            >
              {magicMode
                ? "Sign in by email"
                : mode === "signin"
                  ? "Welcome back."
                  : "Create your account."}
            </h2>
            <p className="mt-2 text-[14px] text-[#102A26]/55">
              {magicMode
                ? "We'll send a one-time link — no password needed."
                : mode === "signin"
                  ? "Enter your credentials to continue."
                  : "Set up access to the document workspace."}
            </p>
          </div>

          {/* Segmented toggle — collapses smoothly (grid-rows 1fr→0fr) in magic-link mode */}
          <div
            className={`grid transition-[grid-template-rows,opacity,margin] duration-300 ease-out ${
              magicMode ? "mt-0 grid-rows-[0fr] opacity-0" : "mt-8 grid-rows-[1fr] opacity-100"
            }`}
          >
            <div className="overflow-hidden">
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
          </div>

          <form
            onSubmit={magicMode ? sendMagicLink : handleAuth}
            className="mt-7"
          >
            <Field
              label="Email"
              type="email"
              required
              autoComplete="email"
              placeholder="name@clinic.org"
              value={email}
              onChange={setEmail}
            />

            {/* Password field — collapses smoothly in magic-link mode (no layout jump).
                Margin is part of the transition so no gap is left behind when hidden. */}
            <div
              className={`grid transition-[grid-template-rows,opacity,margin] duration-300 ease-out ${
                magicMode ? "mt-0 grid-rows-[0fr] opacity-0" : "mt-5 grid-rows-[1fr] opacity-100"
              }`}
            >
              <div className="overflow-hidden">
                <Field
                  label="Password"
                  type="password"
                  required={!magicMode}
                  disabled={magicMode}
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
            </div>

            <button
              type="submit"
              disabled={loading !== null}
              className="group relative mt-5 flex w-full items-center justify-center gap-2 overflow-hidden rounded-xl bg-[#FF5436] py-3.5 text-[14px] font-medium text-[#1A0A06] transition-[transform,background] hover:bg-[#ff6a51] active:translate-y-px disabled:cursor-not-allowed disabled:opacity-60"
            >
              {/* key changes with the label, so the text crossfades on switch */}
              <span
                key={loading !== null ? "working" : magicMode ? "magic" : mode}
                style={{ animation: "fadeIn 1s ease-out" }}
              >
                {loading !== null
                  ? "Working…"
                  : magicMode
                    ? "Send magic link"
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

          {/* Secondary path */}
          <div className="mt-6 flex items-center gap-4 text-[#102A26]/30">
            <span className="h-px flex-1 bg-current" />
            <span className="font-mono text-[10px] uppercase tracking-[0.24em]">
              or
            </span>
            <span className="h-px flex-1 bg-current" />
          </div>

          <button
            type="button"
            onClick={() => {
              setMagicMode((v) => !v);
              setStatus(null);
            }}
            className="mt-6 w-full rounded-xl border border-[#102A26]/15 bg-transparent py-3 text-[13px] font-medium text-[#102A26]/75 transition-colors hover:border-[#102A26]/35 hover:text-[#102A26]"
          >
            {magicMode ? "Back to password sign-in" : "Sign in with a magic link"}
          </button>

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

      <style>{`
        @keyframes sweep {
          to { stroke-dashoffset: -1000; }
        }
        @keyframes travel {
          from { offset-distance: 0%; }
          to { offset-distance: 100%; }
        }
        /* Gentle fade used when the heading text swaps between modes. */
        @keyframes fadeIn {
          from { opacity: 0; transform: translateY(4px); }
          to { opacity: 1; transform: translateY(0); }
        }
      `}</style>
    </main>
  );
}


function Field({
    label,                // label
    value,                // current input value
    onChange,             // function to update value
    ...rest
  }: {
    label: string;
    value: string;
    onChange: (v: string) => void;
  } & Omit<React.InputHTMLAttributes<HTMLInputElement>, "value" | "onChange">) {        // Custom value, onChange
  return (
    <label className="block">
      <span className="mb-1 block font-mono text-[10.5px] uppercase tracking-[0.22em] text-[#102A26]/45">
        {label}
      </span>
      <input
        {...rest}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="w-full border-0 border-b border-[#102A26]/20 bg-transparent pb-1 text-[15px] text-[#102A26] outline-none transition-colors placeholder:text-[#102A26]/30 focus:border-[#FF5436]"
      />
    </label>
  );
}


function Glyph() {
  const stroke = "#E9E4D6";
  return (
    <svg width="26" height="26" viewBox="0 0 26 26" fill="none" aria-hidden>
      <rect x="1" y="1" width="24" height="24" rx="6" stroke={stroke} strokeOpacity="0.5" />
      <path
        d="M5 13h3l2-5 3 10 2-5h6"
        stroke="#FF5436"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}


function PulseLine() {
  const ECG_PATH =
    "M0 100 H650 l30 -60 l34 130 l30 -150 l28 158 l26 -78 H850 l40 -34 l36 68 H1200";

  return (
    <svg viewBox="0 0 1200 200" className="w-full" aria-hidden>
      <path
        d={ECG_PATH}
        fill="none"
        stroke="#FF5436"
        strokeWidth="1.4"
        strokeOpacity="0.2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <path
        className="pulse-beam"
        d={ECG_PATH}
        fill="none"
        stroke="#FF5436"
        strokeWidth="1.6"
        strokeLinecap="round"
        strokeLinejoin="round"
        pathLength={1000}
        style={{
          strokeDasharray: "150 850",
          animation: "sweep 10s linear infinite",
        }}
      />
      <circle
        className="pulse-blip"
        r="4"
        fill="#FF5436"
        style={{
          offsetPath: `path("${ECG_PATH}")`,
          offsetRotate: "0deg",
          filter: "drop-shadow(0 0 15px #FF5436)",
          animation:
            "travel 10s linear infinite, blip 0.5s ease-in-out infinite",
          animationDelay: "-1.5s, 0s",
        }}
      />
    </svg>
  );
}