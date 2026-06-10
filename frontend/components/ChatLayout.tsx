"use client";

import { useCallback, useEffect, useState } from "react";
import { createClient } from "@/lib/utils/browser_client";

type ChatRow = { chat_id: string; chat_name: string };
type MsgRow = { role: string; content: string };

export default function ChatLayout({ userEmail }: { userEmail: string }) {
  const [chats, setChats] = useState<ChatRow[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [messages, setMessages] = useState<MsgRow[]>([]);
  const [input, setInput] = useState("");
  const [loadingList, setLoadingList] = useState(true);
  const [loadingMsgs, setLoadingMsgs] = useState(false);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadChats = useCallback(async () => {
    setError(null);
    const res = await fetch("/api/chats");
    if (!res.ok) {
      const j = await res.json().catch(() => ({}));
      setError(j.error ?? "Could not load chats");
      setChats([]);
      return;
    }
    const data = (await res.json()) as ChatRow[];
    setChats(data);
    setActiveId((prev) => {
      if (prev && data.some((x) => x.chat_id === prev)) {
        return prev;
      }
      return data[0]?.chat_id ?? null;
    });
  }, []);

  const loadMessages = useCallback(async (chatId: string) => {
    setLoadingMsgs(true);
    setError(null);
    const res = await fetch(`/api/chats/${encodeURIComponent(chatId)}/messages`);
    setLoadingMsgs(false);
    if (!res.ok) {
      const j = await res.json().catch(() => ({}));
      setError(j.error ?? "Could not load messages");
      setMessages([]);
      return;
    }
    const data = await res.json();
    setMessages((data.messages ?? []) as MsgRow[]);
  }, []);

  useEffect(() => {
    void (async () => {
      setLoadingList(true);
      await loadChats();
      setLoadingList(false);
    })();
  }, [loadChats]);

  useEffect(() => {
    if (activeId) {
      void loadMessages(activeId);
    }
  }, [activeId, loadMessages]);

  async function newChat() {
    setError(null);
    const res = await fetch("/api/chats", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    });
    if (!res.ok) {
      const j = await res.json().catch(() => ({}));
      setError(j.error ?? "Could not create chat");
      return;
    }
    const row = (await res.json()) as ChatRow;
    setChats((c) => [row, ...c]);
    setActiveId(row.chat_id);
  }

  async function renameChat(chatId: string) {
    const name = window.prompt("New chat name");
    if (!name?.trim()) return;
    const res = await fetch(`/api/chats/${encodeURIComponent(chatId)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ chat_name: name.trim() }),
    });
    if (!res.ok) {
      const j = await res.json().catch(() => ({}));
      setError(j.error ?? "Rename failed");
      return;
    }
    await loadChats();
  }

  async function sendMessage() {
    if (!activeId || !input.trim() || sending) return;
    const text = input.trim();
    setInput("");
    setSending(true);
    setError(null);
    setMessages((m) => [...m, { role: "user", content: text }]);
    const res = await fetch(`/api/chats/${encodeURIComponent(activeId)}/messages`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: text }),
    });
    setSending(false);
    if (!res.ok) {
      const j = await res.json().catch(() => ({}));
      setError(j.error ?? "Send failed");
      await loadMessages(activeId);
      return;
    }
    const data = await res.json();
    const reply = String(data.reply ?? "");
    setMessages((m) => [...m, { role: "assistant", content: reply }]);
  }

  async function signOut() {
    await fetch("/api/auth/logout", { method: "POST" });
    const supabase = createClient();
    await supabase.auth.signOut();
    window.location.href = "/login";
  }

  return (
    <div className="flex h-screen bg-zinc-950 text-zinc-100">
      <aside className="flex w-64 shrink-0 flex-col border-r border-zinc-800 bg-zinc-900/40">
        <div className="border-b border-zinc-800 p-3">
          <button
            type="button"
            onClick={() => void newChat()}
            className="w-full rounded-lg border border-zinc-600 bg-zinc-800 py-2 text-sm hover:bg-zinc-700"
          >
            New chat
          </button>
        </div>
        <div className="flex-1 overflow-y-auto p-2 space-y-1">
          {loadingList ? (
            <p className="px-2 text-xs text-zinc-500">Loading…</p>
          ) : chats.length === 0 ? (
            <p className="px-2 text-xs text-zinc-500">No chats yet.</p>
          ) : (
            chats.map((c) => (
              <div key={c.chat_id} className="group flex gap-1">
                <button
                  type="button"
                  onClick={() => setActiveId(c.chat_id)}
                  className={`min-w-0 flex-1 truncate rounded-md px-2 py-2 text-left text-sm ${
                    c.chat_id === activeId
                      ? "bg-zinc-800 text-white"
                      : "text-zinc-300 hover:bg-zinc-800/60"
                  }`}
                >
                  {c.chat_name}
                </button>
                <button
                  type="button"
                  title="Rename"
                  onClick={() => void renameChat(c.chat_id)}
                  className="shrink-0 rounded px-1 text-xs text-zinc-500 opacity-0 hover:text-zinc-300 group-hover:opacity-100"
                >
                  ✎
                </button>
              </div>
            ))
          )}
        </div>
        <div className="border-t border-zinc-800 p-3 text-xs text-zinc-500">
          <p className="truncate text-zinc-400" title={userEmail}>
            {userEmail}
          </p>
          <button
            type="button"
            onClick={() => void signOut()}
            className="mt-2 text-zinc-400 underline hover:text-zinc-200"
          >
            Sign out
          </button>
        </div>
      </aside>

      <main className="relative flex min-w-0 flex-1 flex-col">
        <header className="border-b border-zinc-800 px-4 py-3 text-sm text-zinc-400">
          DocuMedAI chat
        </header>

        <div className="flex-1 overflow-y-auto px-4 py-6 pb-40">
          {error ? (
            <p className="mb-4 rounded-md border border-red-900/50 bg-red-950/40 px-3 py-2 text-sm text-red-200">
              {error}
            </p>
          ) : null}
          {loadingMsgs && activeId ? (
            <p className="text-sm text-zinc-500">Loading messages…</p>
          ) : null}
          <div className="mx-auto max-w-3xl space-y-4">
            {messages.map((m, i) => {
              const isUser = m.role === "user";
              return (
              <div
                key={`${i}-${m.content.slice(0, 20)}`}
                className={`flex ${isUser ? "justify-end" : "justify-start"}`}
              >
                <div
                  className={`max-w-[85%] rounded-2xl px-4 py-2 text-sm leading-relaxed ${
                    isUser
                      ? "bg-zinc-700 text-zinc-50"
                      : "bg-zinc-800 text-zinc-100"
                  }`}
                >
                  {m.content}
                </div>
              </div>
            );
            })}
          </div>
        </div>

        <div className="pointer-events-none absolute bottom-0 left-0 right-0 flex justify-center p-4">
          <div className="pointer-events-auto flex w-full max-w-3xl gap-2 rounded-2xl border border-zinc-700 bg-zinc-900/95 p-2 shadow-lg backdrop-blur">
            <textarea
              rows={1}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  void sendMessage();
                }
              }}
              placeholder={activeId ? "Message…" : "Select or create a chat"}
              disabled={!activeId || sending}
              className="max-h-40 min-h-[44px] flex-1 resize-none bg-transparent px-3 py-2 text-sm text-zinc-100 outline-none placeholder:text-zinc-500"
            />
            <button
              type="button"
              disabled={!activeId || sending || !input.trim()}
              onClick={() => void sendMessage()}
              className="shrink-0 rounded-xl bg-zinc-100 px-4 py-2 text-sm font-medium text-zinc-900 disabled:opacity-40"
            >
              Send
            </button>
          </div>
        </div>
      </main>
    </div>
  );
}
