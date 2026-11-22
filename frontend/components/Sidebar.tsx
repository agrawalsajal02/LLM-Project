"use client";

import type { Conversation } from "@/lib/api";

type Props = {
  chats: Conversation[];
  activeId: string | null;
  onSelect: (id: string) => void;
  onNew: () => void;
  onDelete: (id: string) => void;
};

export default function Sidebar({ chats, activeId, onSelect, onNew, onDelete }: Props) {
  return (
    <aside className="sidebar">
      <button className="new-chat" onClick={onNew}>
        + New chat
      </button>
      <nav className="chat-list">
        {chats.length === 0 && <p className="muted">No chats yet</p>}
        {chats.map((c) => (
          <div
            key={c.id}
            className={`chat-item ${c.id === activeId ? "active" : ""}`}
            onClick={() => onSelect(c.id)}
            title={c.title}
          >
            <span className="chat-title">{c.title}</span>
            <button
              className="delete"
              aria-label={`Delete ${c.title}`}
              onClick={(e) => {
                e.stopPropagation();
                onDelete(c.id);
              }}
            >
              ×
            </button>
          </div>
        ))}
      </nav>
    </aside>
  );
}
