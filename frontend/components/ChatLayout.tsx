"use client";

import { useCallback, useEffect, useState } from "react";
import { createClient } from "@/lib/utils/browser_client";
import ChatSidebar from "@/components/utils/ChatSidebar";
import MessageList from "@/components/utils/MessageList";
import MessageInput from "@/components/utils/MessageInput";
import type { ChatRow, MsgRow } from "@/components/utils/chat-types";

export default function ChatLayout({ userEmail }: { userEmail: string }) {
  const [chats, setChats] = useState<ChatRow[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [messages, setMessages] = useState<MsgRow[]>([]);
  const [input, setInput] = useState("");
  const [loadingList, setLoadingList] = useState(true);
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
    setError(null);
    setMessages([]);   // Clear stale thread so the bar stays centred while we check.
    const res = await fetch(`/api/chats/${encodeURIComponent(chatId)}/messages`);
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

  // Centred hero layout whenever there's nothing rendered in the thread —
  // including while we're still checking the backend for messages. The bar
  // only drops once actual messages exist to render.
  const isEmptyChat = messages.length === 0;

  return (
    <div className="flex h-screen bg-[#F2EEE4] text-[#102A26]">
      <ChatSidebar
        userEmail={userEmail}
        chats={chats}
        activeId={activeId}
        loadingList={loadingList}
        onSelect={setActiveId}
        onNewChat={() => void newChat()}
        onRename={(id) => void renameChat(id)}
        onSignOut={() => void signOut()}
      />

      {/* ── Conversation ────────────────────────────────────────────── */}
      <main className="relative flex min-w-0 flex-1 flex-col">
        <header className="border-b border-[#102A26]/10 px-8 py-5">
          <p className="font-mono text-[10.5px] uppercase tracking-[0.3em] text-[#FF5436]">
            Clinical AI Assistant
          </p>
          <h1
            className="mt-1 text-[1.4rem] font-normal leading-tight tracking-[-0.01em]"
            style={{ fontFamily: "var(--font-display)" }}
          >
            DocuMedAI Chatbot
          </h1>
        </header>

        <MessageList messages={messages} sending={sending} error={error} />

        <MessageInput
          input={input}
          setInput={setInput}
          onSend={() => void sendMessage()}
          activeId={activeId}
          sending={sending}
          isEmptyChat={isEmptyChat}
        />
      </main>
    </div>
  );
}
