import TypingDots from "@/components/utils/TypingDots";
import type { MsgRow } from "@/components/utils/chat-types";

export default function MessageList({
  messages,
  sending,
  error,
}: {
  messages: MsgRow[];
  sending: boolean;
  error: string | null;
}) {
  return (
    <div className="scrollbar-brand relative flex-1 overflow-y-auto px-4 py-6 pb-40">
      {error ? (
        <p className="mx-auto mb-4 flex max-w-3xl items-start gap-2 rounded-lg border border-[#C23A24]/30 bg-[#C23A24]/10 px-3 py-2 text-[13px] text-[#C23A24]">
          <span aria-hidden className="mt-px font-mono">✕</span>
          {error}
        </p>
      ) : null}

      <div className="mx-auto max-w-3xl space-y-5">
        {messages.map((m, i) => {
          const isUser = m.role === "user";
          return (
            <div
              key={`${i}-${m.content.slice(0, 20)}`}
              className={`flex ${isUser ? "justify-end" : "justify-start"}`}
              style={{ animation: "fadeIn 0.4s ease-out" }}
            >
              {isUser ? (
                // User question — wrapped in a dark bubble.
                <div className="max-w-[85%] rounded-2xl bg-[#0E221F] px-4 py-2.5 text-[14px] leading-relaxed text-[#E9E4D6]">
                  {m.content}
                </div>
              ) : (
                // Assistant answer — no box, plain text on the canvas.
                <div className="max-w-[85%] whitespace-pre-wrap text-[14px] leading-relaxed text-[#102A26]">
                  {m.content}
                </div>
              )}
            </div>
          );
        })}

        {/* Processing indicator on the assistant side while a reply is in flight. */}
        {sending ? (
          <div className="flex justify-start" style={{ animation: "fadeIn 0.4s ease-out" }}>
            <TypingDots />
          </div>
        ) : null}
      </div>
    </div>
  );
}
