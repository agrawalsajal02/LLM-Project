export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export type Conversation = { id: string; title: string; created_at: string; updated_at: string };

export type Citation = {
  n: number;
  kind: "doc" | "web";
  snippet: string;
  // doc
  doc_id?: string;
  doc_name?: string;
  page?: number | null;
  scope?: "kb" | "attachment";
  // web
  url?: string;
  title?: string;
};

export type Step = {
  id: number;
  kind: string; // rewrite | plan | search | web | tool | grade | answer | error
  label: string;
  status: "running" | "done" | "error";
  detail?: string | null;
};

export type Message = {
  id: string;
  role: "user" | "assistant";
  content: string;
  citations?: Citation[] | null;
  steps?: Step[] | null;
  created_at: string;
};
export type ConversationDetail = Conversation & { messages: Message[] };

export type DocumentInfo = {
  id: string;
  filename: string;
  content_type: string;
  size_bytes: number;
  kind: "kb" | "attachment";
  conversation_id: string | null;
  status: "processing" | "ready" | "failed";
  error: string | null;
  chunk_count: number;
  page_count: number | null;
  created_at: string;
};

export type SearchHit = {
  doc_id: string;
  doc_name: string;
  page: number | null;
  text: string;
  rrf_score: number;
  bm25_rank: number | null;
  knn_rank: number | null;
  rerank_score: number | null;
};

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      detail = (await res.json()).detail ?? detail;
    } catch {}
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

function upload(url: string, file: File) {
  const form = new FormData();
  form.append("file", file);
  return fetch(url, { method: "POST", body: form }).then((r) => json<DocumentInfo>(r));
}

export const api = {
  listChats: () => fetch(`${API_URL}/api/chats`).then((r) => json<Conversation[]>(r)),
  getChat: (id: string) => fetch(`${API_URL}/api/chats/${id}`).then((r) => json<ConversationDetail>(r)),
  createChat: () => fetch(`${API_URL}/api/chats`, { method: "POST" }).then((r) => json<Conversation>(r)),
  deleteChat: async (id: string) => {
    const r = await fetch(`${API_URL}/api/chats/${id}`, { method: "DELETE" });
    if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  },

  listDocuments: () => fetch(`${API_URL}/api/documents`).then((r) => json<DocumentInfo[]>(r)),
  uploadDocument: (file: File) => upload(`${API_URL}/api/documents`, file),
  deleteDocument: async (id: string) => {
    const r = await fetch(`${API_URL}/api/documents/${id}`, { method: "DELETE" });
    if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  },
  promoteDocument: (id: string) =>
    fetch(`${API_URL}/api/documents/${id}/promote`, { method: "POST" }).then((r) => json<DocumentInfo>(r)),
  search: (q: string) =>
    fetch(`${API_URL}/api/search?q=${encodeURIComponent(q)}`).then((r) => json<SearchHit[]>(r)),

  listAttachments: (chatId: string) =>
    fetch(`${API_URL}/api/chats/${chatId}/attachments`).then((r) => json<DocumentInfo[]>(r)),
  uploadAttachment: (chatId: string, file: File) => upload(`${API_URL}/api/chats/${chatId}/attachments`, file),
};

export function documentUrl(docId: string, page?: number | null) {
  return `${API_URL}/api/documents/${docId}/file${page ? `#page=${page}` : ""}`;
}

export function citationHref(c: Citation): string | undefined {
  if (c.kind === "web") return c.url;
  if (c.doc_id) return documentUrl(c.doc_id, c.page);
  return undefined;
}

export type StreamHandlers = {
  onToken: (text: string) => void;
  onStep: (step: Step) => void;
  onCitations: (citations: Citation[]) => void;
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
      else if (event === "step") handlers.onStep(payload);
      else if (event === "citations") handlers.onCitations(payload.citations);
      else if (event === "done") handlers.onDone(payload);
      else if (event === "error") handlers.onError(payload.detail);
    }
  }
}
