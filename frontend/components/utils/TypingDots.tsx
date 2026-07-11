// Three dots jumping up/down — a "system is processing" indicator.
// Animation keyframe `dotJump` lives in globals.css.
export default function TypingDots() {
  return (
    <div className="flex items-center gap-1.5 py-1" aria-label="Assistant is typing">
      <span className="h-2 w-2 rounded-full bg-[#102A26]/40 [animation:dotJump_1s_ease-in-out_infinite]" />
      <span className="h-2 w-2 rounded-full bg-[#102A26]/40 [animation:dotJump_1s_ease-in-out_infinite] [animation-delay:0.15s]" />
      <span className="h-2 w-2 rounded-full bg-[#102A26]/40 [animation:dotJump_1s_ease-in-out_infinite] [animation-delay:0.3s]" />
    </div>
  );
}
