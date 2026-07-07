"use client";

import { useEffect, useRef } from "react";
import { PulseLine } from "@/components/utils/BrandPanel";

export default function MessageInput({
  input,
  setInput,
  onSend,
  onUploadFile,
  uploading,
  activeId,
  sending,
  isEmptyChat,
}: {
  input: string;
  setInput: (v: string) => void;
  onSend: () => void;
  onUploadFile: (file: File) => void;
  uploading: boolean;
  activeId: string | null;
  sending: boolean;
  isEmptyChat: boolean;
}) {
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Auto-grow the message box upward as the text wraps: reset to the natural
  // single-row height, then expand to fit content (capped by max-h via CSS).
  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${el.scrollHeight}px`;
  }, [input]);

  return (
    <div
      className={`pointer-events-none absolute inset-x-0 bottom-0 flex flex-col items-center px-4 pb-4 transition-transform duration-700 ease-out ${
        isEmptyChat ? "-translate-y-[38vh]" : "translate-y-0"
      }`}
    >
      {/* Hero — shown on an empty chat, sitting just above the bar. Tracks the
          bar exactly: stays put while loading, fades out once messages render. */}
      <div
        className={`pointer-events-none mb-6 flex w-full max-w-lg flex-col items-center text-center transition-opacity duration-500 ${
          isEmptyChat ? "opacity-70" : "h-0 opacity-0"
        }`}
      >
        <PulseLine centered />
        <p
          className="mt-3 text-[1.5rem] font-light italic text-[#102A26]/55"
          style={{ fontFamily: "var(--font-display)" }}
        >
          Ask anything.
        </p>
      </div>

      {/* Two rectangles, equal height at rest (min-h-[52px]). The message box
          grows UPWARD with content; the send button stays fixed and
          bottom-aligned, so only the bar expands. */}
      <div className="pointer-events-auto flex w-full max-w-3xl items-end gap-2">
        {/* Hidden picker + round "+" button: upload a document (pdf/docx/txt). */}
        <input
          ref={fileInputRef}
          type="file"
          accept=".pdf,.docx,.txt"
          className="hidden"
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) onUploadFile(file);
            e.target.value = ""; // allow re-picking the same file
          }}
        />
        <button
          type="button"
          disabled={!activeId || uploading}
          onClick={() => fileInputRef.current?.click()}
          title="Upload a document (PDF, DOCX, TXT)"
          aria-label="Upload a document"
          className="flex h-[52px] w-[52px] shrink-0 items-center justify-center rounded-full border border-[#102A26]/15 bg-white/80 text-[32px] font-light leading-none text-[#102A26] shadow-lg backdrop-blur transition-[transform,background,color] hover:border-[#FF5436]/60 hover:text-[#FF5436] active:translate-y-px disabled:cursor-not-allowed disabled:opacity-40"
        >
          {uploading ? (
            <span className="h-5 w-5 animate-spin rounded-full border-2 border-[#102A26]/30 border-t-[#FF5436]" />
          ) : (
            <span aria-hidden>+</span>
          )}
        </button>
        <div className="flex min-h-[52px] flex-1 items-center rounded-2xl border border-[#102A26]/15 bg-white/80 px-1 py-1 shadow-lg backdrop-blur transition-colors focus-within:border-[#FF5436]/60">
          <textarea
            ref={textareaRef}
            rows={1}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                onSend();
              }
            }}
            placeholder={activeId ? "Message…" : "Select or create a chat"}
            // Stays enabled while sending so the user can keep typing —
            // only the Send button locks during a request.
            disabled={!activeId}
            className="scrollbar-brand max-h-40 w-full resize-none bg-transparent px-3 py-2 text-[14px] leading-relaxed text-[#102A26] outline-none placeholder:text-[#102A26]/35"
          />
        </div>
        <button
          type="button"
          disabled={!activeId || sending || !input.trim()}
          onClick={onSend}
          className="group flex h-[52px] shrink-0 items-center gap-1.5 rounded-2xl border border-[#102A26]/15 bg-[#FF5436] px-4 text-[14px] font-medium text-[#1A0A06] shadow-lg backdrop-blur transition-[transform,background] hover:bg-[#ff6a51] active:translate-y-px disabled:cursor-not-allowed disabled:opacity-40"
        >
          Send
          <span aria-hidden className="transition-transform duration-300 group-hover:translate-x-1">
            →
          </span>
        </button>
      </div>
    </div>
  );
}
