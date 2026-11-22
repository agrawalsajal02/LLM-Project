export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export type Conversation = { id: string; title: string; created_at: string; updated_at: string };
export type Message = { id: string; role: "user" | "assistant"; content: string; created_at: string };
export type ConversationDetail = Conversation & { messages: Message[] };

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json() as Promise<T>;
}

export const api = {
  listChats: () => fetch(`${API_URL}/api/chats`).then((r) => json<Conversation[]>(r)),
  getChat: (id: string) => fetch(`${API_URL}/api/chats/${id}`).then((r) => json<ConversationDetail>(r)),
  createChat: () => fetch(`${API_URL}/api/chats`, { method: "POST" }).then((r) => json<Conversation>(r)),
  deleteChat: async (id: string) => {
    const r = await fetch(`${API_URL}/api/chats/${id}`, { method: "DELETE" });
    if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  },
};

export type StreamHandlers = {
  onToken: (text: string) => void;
  onDone: (data: { message_id: string | null; title: string | null }) => void;
  onError: (detail: string) => void;
};

/**
 * POSTs a message and reads the Server-Sent Events stream.
 * (EventSource only supports GET, so we parse the stream from fetch ourselves.)
 */
export async function streamMessage(
  chatId: string,
  content: string,
  handlers: StreamHandlers,
  signal: AbortSignal,
): Promise<void> {
  const res = await fetch(`${API_URL}/api/chats/${chatId}/messages`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content }),
    signal,
  });
  if (!res.ok || !res.body) throw new Error(`${res.status} ${res.statusText}`);

  const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += value;

    // Events are separated by a blank line.
    let sep: number;
    while ((sep = buffer.indexOf("\n\n")) !== -1) {
      const raw = buffer.slice(0, sep);
      buffer = buffer.slice(sep + 2);

      let event = "message";
      let data = "";
      for (const line of raw.split("\n")) {
        if (line.startsWith("event:")) event = line.slice(6).trim();
        else if (line.startsWith("data:")) data += line.slice(5).trim();
      }
      if (!data) continue;
      const payload = JSON.parse(data);
      if (event === "token") handlers.onToken(payload.text);
      else if (event === "done") handlers.onDone(payload);
      else if (event === "error") handlers.onError(payload.detail);
    }
  }
}
