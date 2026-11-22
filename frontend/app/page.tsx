"use client";

import { ChangeEvent, FormEvent, KeyboardEvent, useCallback, useEffect, useRef, useState } from "react";

import MessageBubble from "@/components/MessageBubble";
import Sidebar from "@/components/Sidebar";
import { api, Citation, Conversation, DocumentInfo, Step, streamMessage } from "@/lib/api";

type UiMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
  citations?: Citation[] | null;
  steps?: Step[] | null;
};

const ATTACH_ACCEPT = ".pdf,.docx,.txt,.md,.csv,.xlsx,.xls,.png,.jpg,.jpeg,.webp";

export default function ChatPage() {
  const [chats, setChats] = useState<Conversation[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [messages, setMessages] = useState<UiMessage[]>([]);
  const [attachments, setAttachments] = useState<DocumentInfo[]>([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);

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

  // Poll attachment status while any are still processing.
  useEffect(() => {
    if (!activeId || !attachments.some((a) => a.status === "processing")) return;
    const t = setInterval(async () => setAttachments(await api.listAttachments(activeId)), 1500);
    return () => clearInterval(t);
  }, [activeId, attachments]);

  async function openChat(id: string) {
    if (streaming) return;
    setError(null);
    setActiveId(id);
    const [detail, files] = await Promise.all([api.getChat(id), api.listAttachments(id)]);
    setMessages(detail.messages.map(({ id, role, content, citations, steps }) => ({ id, role, content, citations, steps })));
    setAttachments(files);
  }

  function newChat() {
    if (streaming) return;
    setActiveId(null);
    setMessages([]);
    setAttachments([]);
    setError(null);
  }

  async function deleteChat(id: string) {
    await api.deleteChat(id);
    if (id === activeId) newChat();
    refreshChats();
  }

  async function ensureChat(): Promise<string> {
    if (activeId) return activeId;
    const chat = await api.createChat();
    setActiveId(chat.id);
    setChats((prev) => [chat, ...prev]);
    return chat.id;
  }

  async function onAttach(e: ChangeEvent<HTMLInputElement>) {
    const files = Array.from(e.target.files ?? []);
    e.target.value = "";
    if (!files.length) return;
    setUploading(true);
    setError(null);
    try {
      const chatId = await ensureChat();
      for (const f of files) {
        const doc = await api.uploadAttachment(chatId, f);
        setAttachments((prev) => [...prev, doc]);
      }
    } catch (err) {
      setError("Upload failed: " + (err as Error).message);
    } finally {
      setUploading(false);
    }
  }

  async function send(e?: FormEvent) {
    e?.preventDefault();
    const content = input.trim();
    if (!content || streaming) return;
    setError(null);
    setInput("");

    const chatId = await ensureChat();
    const assistantId = crypto.randomUUID();
    setMessages((prev) => [
      ...prev,
      { id: crypto.randomUUID(), role: "user", content },
      { id: assistantId, role: "assistant", content: "", steps: [], citations: [] },
    ]);

    const update = (fn: (m: UiMessage) => UiMessage) =>
      setMessages((prev) => prev.map((m) => (m.id === assistantId ? fn(m) : m)));

    const controller = new AbortController();
    abortRef.current = controller;
    setStreaming(true);
    try {
      await streamMessage(
        chatId,
        content,
        {
          onToken: (text) => update((m) => ({ ...m, content: m.content + text })),
          onStep: (step) =>
            update((m) => {
              const steps = m.steps ?? [];
              const i = steps.findIndex((s) => s.id === step.id);
              return { ...m, steps: i >= 0 ? steps.map((s, j) => (j === i ? step : s)) : [...steps, step] };
            }),
          onCitations: (citations) => update((m) => ({ ...m, citations })),
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
              <h1>Dark Store Ops Copilot</h1>
              <p className="muted">Ask about SOPs, inventory, expiry, or attach a file.</p>
              <div className="suggestions">
                {[
                  "Which dairy items expire this week?",
                  "What is the SOP for expiring dairy?",
                  "Which SKUs are below reorder level?",
                ].map((s) => (
                  <button key={s} onClick={() => setInput(s)}>
                    {s}
                  </button>
                ))}
              </div>
            </div>
          )}
          {messages.map((m, i) => (
            <MessageBubble
              key={m.id}
              role={m.role}
              content={m.content}
              citations={m.citations}
              steps={m.steps}
              streaming={streaming && i === messages.length - 1 && m.role === "assistant"}
            />
          ))}
          {error && <div className="error">{error}</div>}
          <div ref={bottomRef} />
        </div>

        {attachments.length > 0 && (
          <div className="attachments">
            {attachments.map((a) => (
              <span key={a.id} className={`attachment ${a.status}`} title={a.error ?? a.status}>
                📎 {a.filename}
                {a.status === "processing" && " …"}
                {a.status === "failed" && " ⚠️"}
              </span>
            ))}
          </div>
        )}

        <form className="composer" onSubmit={send}>
          <input ref={fileRef} type="file" accept={ATTACH_ACCEPT} multiple hidden onChange={onAttach} />
          <button
            type="button"
            className="attach"
            onClick={() => fileRef.current?.click()}
            disabled={uploading || streaming}
            title="Attach a file to this chat (PDF, DOCX, TXT, CSV, Excel, image)"
            aria-label="Attach file"
          >
            {uploading ? "…" : "📎"}
          </button>
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={onKeyDown}
            placeholder="Send a message (Enter to send, Shift+Enter for a new line)"
            rows={1}
          />
          {streaming ? (
            <button type="button" onClick={() => abortRef.current?.abort()} className="stop">
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
