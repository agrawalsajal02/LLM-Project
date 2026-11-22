"use client";

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

type Props = { role: "user" | "assistant"; content: string; streaming?: boolean };

export default function MessageBubble({ role, content, streaming }: Props) {
  return (
    <div className={`message ${role}`}>
      <div className="bubble">
        {role === "assistant" ? (
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{content || (streaming ? "…" : "")}</ReactMarkdown>
        ) : (
          <p>{content}</p>
        )}
        {streaming && content && <span className="cursor">▍</span>}
      </div>
    </div>
  );
}
