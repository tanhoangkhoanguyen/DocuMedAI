// Presentational left-hand brand panel for the auth screens, plus its SVG
// helpers (Glyph + the animated ECG PulseLine). No state — pure design.


// ── Brand panel ─────────────────────────────────────────────── */
export default function BrandPanel() {
  return (
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
  );
}


export function Glyph() {
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


export function PulseLine({ centered = false }: { centered?: boolean } = {}) {
  // Default path keeps the spikes right-of-centre (used by the login hero).
  // `centered` shifts the rough/zigzag part to the horizontal middle (x≈600)
  // for the chat empty-state, where it sits above a centred input bar.
  const ECG_PATH = centered
    ? "M0 100 H470 l30 -60 l34 130 l30 -150 l28 158 l26 -78 H670 l40 -34 l36 68 H1200"
    : "M0 100 H650 l30 -60 l34 130 l30 -150 l28 158 l26 -78 H850 l40 -34 l36 68 H1200";

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
