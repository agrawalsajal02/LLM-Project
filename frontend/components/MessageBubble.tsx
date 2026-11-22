"use client";

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import AgentSteps from "@/components/AgentSteps";
import { Citation, citationHref, Step } from "@/lib/api";

type Props = {
  role: "user" | "assistant";
  content: string;
  streaming?: boolean;
  citations?: Citation[] | null;
  steps?: Step[] | null;
};

/** Turns "[2]" in the answer into a link to source 2 (the PDF page or the web page). */
function linkCitations(text: string, citations: Citation[]) {
  const byN = new Map(citations.map((c) => [c.n, c]));
  return text.replace(/\[(\d+)\](?!\()/g, (match, n) => {
    const c = byN.get(Number(n));
    const href = c && citationHref(c);
    return href ? `[\\[${n}\\]](${href})` : match;
  });
}

function label(c: Citation) {
  if (c.kind === "web") {
    try {
      return c.title || new URL(c.url!).hostname;
    } catch {
      return c.url;
    }
  }
  return `${c.doc_name}${c.page ? `, p.${c.page}` : ""}`;
}

export default function MessageBubble({ role, content, streaming, citations, steps }: Props) {
  const cites = citations ?? [];
  return (
    <div className={`message ${role}`}>
      <div className="bubble">
        {role === "assistant" && steps && steps.length > 0 && <AgentSteps steps={steps} live={!!streaming && !content} />}
        {role === "assistant" ? (
          <ReactMarkdown
            remarkPlugins={[remarkGfm]}
            components={{ a: (props) => <a {...props} target="_blank" rel="noreferrer" /> }}
          >
            {linkCitations(content, cites) || (streaming && !steps?.length ? "…" : "")}
          </ReactMarkdown>
        ) : (
          <p>{content}</p>
        )}
        {streaming && content && <span className="cursor">▍</span>}
        {cites.length > 0 && (
          <div className="citations">
            {cites.map((c) => (
              <a
                key={c.n}
                className={`citation ${c.kind}`}
                href={citationHref(c)}
                target="_blank"
                rel="noreferrer"
                title={c.snippet}
              >
                <span className="citation-n">{c.n}</span>
                {c.kind === "web" ? "🌐 " : c.scope === "attachment" ? "📎 " : "📄 "}
                {label(c)}
              </a>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
