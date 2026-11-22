# Phase 1 — ChatGPT-style Basic Chat (Streaming)

**Release tag:** `v0.1-chat` · [← Back to roadmap](../../README.md) · [Next: Phase 2 →](../phase-2-rag-hybrid-search/README.md)

## ▶️ Run It (code is in [`backend/`](../../backend) and [`frontend/`](../../frontend))

Get a free Gemini API key from [Google AI Studio](https://aistudio.google.com/apikey). No key yet? Set `LLM_PROVIDER=fake` to try the app with a canned streaming reply.

**Option A: Docker Compose (everything in one command)**

```bash
cp .env.example .env   # then put your GEMINI_API_KEY in .env
docker compose up --build
```

Open http://localhost:3000 (API docs at http://localhost:8000/docs).

**Option B: run locally for development**

```bash
docker compose up -d postgres
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp ../.env.example .env   # add GEMINI_API_KEY
alembic upgrade head
uvicorn app.main:app --reload
```

In a second terminal:

```bash
cd frontend
npm install
npm run dev
```

**Tests** (SQLite + fake model, no Postgres or API key needed):

```bash
cd backend && pytest -q
```

### What's Implemented

| File | Purpose |
|---|---|
| `backend/app/main.py` | FastAPI app, CORS, `/health` |
| `backend/app/config.py` | Settings from env / `.env` |
| `backend/app/models.py` | `Conversation`, `Message` tables |
| `backend/alembic/` | Migrations (run automatically in Docker) |
| `backend/app/services/llm.py` | `GeminiClient` (streaming) + `FakeClient`, behind one interface |
| `backend/app/services/chat.py` | History window, saving messages, auto-title |
| `backend/app/api/chats.py` | REST endpoints + SSE streaming; saves partial replies on stop/disconnect |
| `backend/tests/` | API tests: CRUD, streaming, history, error event |
| `frontend/lib/api.ts` | API client + SSE stream parser over `fetch` |
| `frontend/app/page.tsx` | Chat page: streaming, stop, new chat, reopen chats |
| `frontend/components/` | Sidebar and Markdown message bubbles |
| `.github/workflows/ci.yml` | Runs backend tests and frontend build on every push/PR |

## Goal

Build a ChatGPT-like chat app where answers **stream token by token**, conversations are **saved**, and the user can start a **new chat** or reopen **old chats**.

## Features

| Feature | Description |
|---|---|
| Live token streaming | Tokens appear in the UI as the LLM generates them (SSE) |
| Conversation history | Every message is stored and replayed as context on the next turn |
| New chat | Creates a new conversation with an auto-generated title |
| Chat list | Sidebar listing past chats, sorted by last activity |
| Stop generation | Cancel an in-flight stream |
| Markdown rendering | Code blocks, lists and tables rendered in the UI |

## Architecture

```mermaid
sequenceDiagram
    participant UI as Next.js UI
    participant API as FastAPI
    participant DB as PostgreSQL
    participant LLM as Gemini API
    UI->>API: POST /chats/{id}/messages (user message)
    API->>DB: save user message, load history
    API->>LLM: stream generate(history + message)
    loop each token
        LLM-->>API: token
        API-->>UI: SSE event: token
    end
    API->>DB: save full assistant message
    API-->>UI: SSE event: done
```

## Tools & Why

| Tool | Role in this phase | Notes |
|---|---|---|
| **Next.js (React)** | Chat UI, sidebar, streaming rendering | Read the stream with `fetch` + `ReadableStream` or `EventSource`; `react-markdown` for rendering |
| **FastAPI** | Async REST API + SSE endpoint | `StreamingResponse` or `sse-starlette`'s `EventSourceResponse` |
| **Gemini API** | The LLM | `google-genai` SDK (`generate_content_stream`) or LangChain `ChatGoogleGenerativeAI.astream()` |
| **PostgreSQL** | Stores chats and messages | Use `asyncpg` driver |
| **SQLAlchemy 2.0 + Alembic** | ORM and DB migrations | Async sessions |
| **Pydantic / pydantic-settings** | Request/response schemas, config from `.env` | Keep the API key out of code |
| **Docker Compose** | Runs Postgres (and later everything) locally | One `docker compose up` |

## Data Model

```sql
conversations (id UUID PK, title TEXT, created_at TIMESTAMPTZ, updated_at TIMESTAMPTZ)
messages      (id UUID PK, conversation_id UUID FK, role TEXT CHECK (role IN ('user','assistant','system')),
               content TEXT, tokens_in INT, tokens_out INT, created_at TIMESTAMPTZ)
```

## API Endpoints

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/chats` | Create a new conversation |
| `GET` | `/api/chats` | List conversations |
| `GET` | `/api/chats/{id}` | Get a conversation with its messages |
| `POST` | `/api/chats/{id}/messages` | Send a message, response is an SSE stream |
| `DELETE` | `/api/chats/{id}` | Delete a conversation |

**SSE event format:** `event: token` / `data: {"text": "..."}`, then `event: done` / `data: {"message_id": "..."}`; on failure `event: error`.

## Suggested Folder Structure

```
backend/
  app/
    main.py            # FastAPI app, CORS, routers
    config.py          # pydantic-settings
    db/                # models.py, session.py, alembic/
    api/chats.py       # routes
    services/llm.py    # Gemini streaming wrapper
    services/chat.py   # history building, persistence
frontend/
  app/ (Next.js)       # chat page, sidebar, message components
docker-compose.yml
```

## Implementation Steps

1. Docker Compose with Postgres; FastAPI skeleton with `/health`.
2. SQLAlchemy models + first Alembic migration.
3. Chat CRUD endpoints.
4. Gemini streaming wrapper (async generator yielding text chunks).
5. SSE endpoint: save user message → stream → save assistant message.
6. History window: send the last N messages (or trim by token count) as context.
7. Auto-title the chat from the first message (one short LLM call).
8. Next.js UI: sidebar, chat view, streaming render, stop button.

## Key Concepts to Learn

- How SSE works vs WebSockets, and why SSE fits one-way token streaming.
- Async generators in Python and back-pressure.
- Context window limits and history trimming.
- Handling client disconnects (stop generation cleanly).

## Definition of Done / Demo

- [x] Answer streams token by token in the UI
- [x] Reloading the page keeps the conversation
- [x] Sidebar shows old chats; clicking one reopens it
- [x] "New chat" works; stop button cancels the stream

## Interview Talking Points

- Why SSE over WebSockets for streaming LLM output.
- How history is stored and trimmed to fit the context window.
- How you handled errors mid-stream and client disconnects.
