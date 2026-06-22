import { Glyph } from "@/components/utils/BrandPanel";
import type { ChatRow } from "@/components/utils/chat-types";

export default function ChatSidebar({
  userEmail,
  chats,
  activeId,
  loadingList,
  onSelect,
  onNewChat,
  onRename,
  onSignOut,
}: {
  userEmail: string;
  chats: ChatRow[];
  activeId: string | null;
  loadingList: boolean;
  onSelect: (chatId: string) => void;
  onNewChat: () => void;
  onRename: (chatId: string) => void;
  onSignOut: () => void;
}) {
  return (
    <aside className="flex w-72 shrink-0 flex-col bg-[#0E221F] text-[#E9E4D6]">
      {/* Brand header */}
      <header className="flex items-center gap-3 border-b border-[#2C453F] px-5 py-4">
        <Glyph />
        <span className="font-mono text-[11px] uppercase tracking-[0.32em] text-[#8FB3A6]">
          DocuMedAI
        </span>
      </header>

      {/* Account card — pinned to the top so it reads as a distinct zone,
          visually separated from the chat list below. */}
      <div className="m-3 rounded-xl border border-[#2C453F] bg-[#E9E4D6]/[0.04] p-3">
        <div className="flex items-center gap-2.5">
          <span
            aria-hidden
            className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-[#FF5436] text-[13px] font-semibold uppercase text-[#1A0A06]"
          >
            {(userEmail || "?").charAt(0)}
          </span>
          <p className="min-w-0 flex-1 truncate text-[13px] text-[#E9E4D6]" title={userEmail}>
            {userEmail}
          </p>
        </div>
        <button
          type="button"
          onClick={onSignOut}
          className="mt-3 w-full rounded-lg border border-[#2C453F] py-2 font-mono text-[10px] uppercase tracking-[0.2em] text-[#8FB3A6] transition-colors hover:border-[#FF5436]/50 hover:text-[#FF5436]"
        >
          Sign out
        </button>
      </div>

      {/* New chat button */}
      <div className="px-3 pb-2">
        <button
          type="button"
          onClick={onNewChat}
          className="group flex w-full items-center justify-center gap-2 rounded-xl bg-[#FF5436] py-2.5 text-[13px] font-medium text-[#1A0A06] transition-[transform,background] hover:bg-[#ff6a51] active:translate-y-px"
        >
          New chat
          <span aria-hidden className="transition-transform duration-300 group-hover:translate-x-1">
            +
          </span>
        </button>
      </div>

      <div className="scrollbar-brand-dark flex-1 space-y-1 overflow-y-auto px-2 pb-2">
        {loadingList ? (
          <p className="px-3 py-2 font-mono text-[10px] uppercase tracking-[0.2em] text-[#6E8A82]">
            Loading…
          </p>
        ) : chats.length === 0 ? (
          <p className="px-3 py-2 font-mono text-[10px] uppercase tracking-[0.2em] text-[#6E8A82]">
            No chats yet.
          </p>
        ) : (
          chats.map((c) => {
            const isActive = c.chat_id === activeId;
            return (
              <div key={c.chat_id} className="group flex items-stretch gap-1">
                <button
                  type="button"
                  onClick={() => onSelect(c.chat_id)}
                  className={`relative min-w-0 flex-1 truncate rounded-lg px-3 py-2 text-left text-[13px] transition-colors ${
                    isActive
                      ? "bg-[#E9E4D6]/10 text-[#E9E4D6]"
                      : "text-[#8FB3A6] hover:bg-[#E9E4D6]/[0.06] hover:text-[#E9E4D6]"
                  }`}
                >
                  {/* coral left-accent marks the active chat */}
                  <span
                    aria-hidden
                    className={`absolute inset-y-1.5 left-0 w-[3px] rounded-full bg-[#FF5436] transition-opacity ${
                      isActive ? "opacity-100" : "opacity-0"
                    }`}
                  />
                  {c.chat_name}
                </button>
                <button
                  type="button"
                  title="Rename"
                  onClick={() => onRename(c.chat_id)}
                  className="shrink-0 rounded px-1 text-xs text-[#6E8A82] opacity-0 transition hover:text-[#FF5436] group-hover:opacity-100"
                >
                  ✎
                </button>
              </div>
            );
          })
        )}
      </div>
    </aside>
  );
}
