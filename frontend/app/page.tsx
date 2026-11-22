"use client";

import { FormEvent, KeyboardEvent, useCallback, useEffect, useRef, useState } from "react";

import MessageBubble from "@/components/MessageBubble";
import Sidebar from "@/components/Sidebar";
import { api, Conversation, streamMessage } from "@/lib/api";

type UiMessage = { id: string; role: "user" | "assistant"; content: string };

export default function ChatPage() {
  const [chats, setChats] = useState<Conversation[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [messages, setMessages] = useState<UiMessage[]>([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  const refreshChats = useCallback(async () => {
    try {
      setChats(await api.listChats());
    } catch {
      setError("Can't reach the backend. Is it running on " + (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000") + "?");
    }
  }, []);

  useEffect(() => {
    refreshChats();
  }, [refreshChats]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  async function openChat(id: string) {
    if (streaming) return;
    setError(null);
    setActiveId(id);
    const detail = await api.getChat(id);
    setMessages(detail.messages.map(({ id, role, content }) => ({ id, role, content })));
  }

  function newChat() {
    if (streaming) return;
    setActiveId(null);
    setMessages([]);
    setError(null);
  }

  async function deleteChat(id: string) {
    await api.deleteChat(id);
    if (id === activeId) newChat();
    refreshChats();
  }

  async function send(e?: FormEvent) {
    e?.preventDefault();
    const content = input.trim();
    if (!content || streaming) return;
    setError(null);
    setInput("");

    let chatId = activeId;
    if (!chatId) {
      const chat = await api.createChat();
      chatId = chat.id;
      setActiveId(chat.id);
      setChats((prev) => [chat, ...prev]);
    }

    const assistantId = crypto.randomUUID();
    setMessages((prev) => [
      ...prev,
      { id: crypto.randomUUID(), role: "user", content },
      { id: assistantId, role: "assistant", content: "" },
    ]);

    const appendToAssistant = (text: string) =>
      setMessages((prev) => prev.map((m) => (m.id === assistantId ? { ...m, content: m.content + text } : m)));

    const controller = new AbortController();
    abortRef.current = controller;
    setStreaming(true);
    try {
      await streamMessage(
        chatId,
        content,
        {
          onToken: appendToAssistant,
          onDone: () => refreshChats(),
          onError: (detail) => setError(detail),
        },
        controller.signal,
      );
    } catch (err) {
      if ((err as Error).name !== "AbortError") setError("Streaming failed: " + (err as Error).message);
    } finally {
      setStreaming(false);
      abortRef.current = null;
      refreshChats();
    }
  }

  function stop() {
    abortRef.current?.abort();
  }

  function onKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  }

  return (
    <div className="layout">
      <Sidebar chats={chats} activeId={activeId} onSelect={openChat} onNew={newChat} onDelete={deleteChat} />

      <main className="chat">
        <div className="messages">
          {messages.length === 0 && (
            <div className="empty">
              <h1>How can I help?</h1>
              <p className="muted">Ask anything. Answers stream in live.</p>
            </div>
          )}
          {messages.map((m, i) => (
            <MessageBubble
              key={m.id}
              role={m.role}
              content={m.content}
              streaming={streaming && i === messages.length - 1 && m.role === "assistant"}
            />
          ))}
          {error && <div className="error">{error}</div>}
          <div ref={bottomRef} />
        </div>

        <form className="composer" onSubmit={send}>
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={onKeyDown}
            placeholder="Send a message (Enter to send, Shift+Enter for a new line)"
            rows={1}
          />
          {streaming ? (
            <button type="button" onClick={stop} className="stop">
              Stop
            </button>
          ) : (
            <button type="submit" disabled={!input.trim()}>
              Send
            </button>
          )}
        </form>
      </main>
    </div>
  );
}
