# Phase 2 — RAG + Hybrid Search

**Release tag:** `v0.2-rag` · [← Phase 1](../phase-1-streaming-chat/README.md) · [Back to roadmap](../../README.md) · [Next: Phase 3 →](../phase-3-agent-workflows/README.md)

## ▶️ Run It (code is in [`backend/`](../../backend) and [`frontend/`](../../frontend))

Phase 2 adds **Elasticsearch** to Docker Compose and a **Knowledge base** page in the UI.

```bash
cp .env.example .env   # set GEMINI_API_KEY (used for answers and embeddings)
docker compose up --build
```

Open http://localhost:3000/documents, upload a PDF/DOCX/TXT/MD, wait for **ready**, then ask about it in the chat.
To see Phase 2 on its own (always search the docs, no agent), set `CHAT_MODE=rag`.

**No API key / low RAM?** Each part can run free and locally:

| Setting | Options |
|---|---|
| `EMBEDDING_PROVIDER` | `gemini` (free tier) · `local` (bge-small on CPU: `pip install -r requirements-local-models.txt`) · `fake` (tests) |
| `SEARCH_BACKEND` | `elasticsearch` · `memory` (pure-Python BM25 + vectors saved to a JSON file, no extra service) |
| `RERANKER` | `none` · `local` (bge-reranker cross-encoder on CPU) |
| `PARSER` | `local` (pypdf / python-docx) · `tika` (`docker compose --profile tika up`) |

> Changing the embedding provider or `EMBEDDING_DIMS` changes the vector size: delete the `chunks` index (`curl -XDELETE localhost:9200/chunks`) and re-upload.

**Tests** (in-memory search, fake model; add `TEST_SEARCH_BACKEND=elasticsearch` to run them against a real ES):

```bash
cd backend && pytest -q
```

### What's Implemented

| File | Purpose |
|---|---|
| `backend/app/services/parsing.py` | PDF (per page), DOCX (text + tables), TXT/MD, CSV/Excel → text sections; optional Tika |
| `backend/app/services/chunking.py` | Recursive splitter (paragraph → line → sentence → word) with overlap; chunks never cross pages |
| `backend/app/services/embeddings.py` | Gemini / local sentence-transformers / fake hashing embedder behind one interface |
| `backend/app/services/search_store.py` | `ElasticsearchStore` (BM25 `match` + `dense_vector` kNN, scope filter) and `MemoryStore` |
| `backend/app/services/retrieval.py` | Hybrid search: BM25 + kNN in parallel → **RRF** in Python (works on any ES license) → optional reranker |
| `backend/app/services/ingestion.py` | Upload → parse → chunk → embed → bulk index, in a background task; status `processing/ready/failed` |
| `backend/app/api/documents.py` | Upload/list/delete docs, serve the original file (for citation links), `/api/search` debug endpoint |
| `backend/alembic/versions/0002_*` | `documents` table, `citations` + `steps` columns on messages |
| `frontend/app/documents/page.tsx` | Knowledge base page: drag-and-drop upload, live status, hybrid-search tester showing BM25/kNN/RRF |
| `frontend/components/MessageBubble.tsx` | Citation chips; `[n]` in the answer links to the PDF page or web page |

## Goal

Let users **upload documents (PDF/DOCX)** and ask questions about them. Retrieval combines **keyword search (BM25)** and **vector search (kNN)**, merged with **Reciprocal Rank Fusion (RRF)**. Answers show **citations**.

## Features

| Feature | Description |
|---|---|
| Document upload | PDF/DOCX upload with status (processing / ready / failed) |
| Parsing | Extract text, keep page numbers and headings |
| Chunking | Split text into overlapping chunks with metadata |
| Embedding | Convert each chunk into a dense vector |
| Hybrid search | BM25 + kNN, merged with RRF |
| Reranking (bonus) | Cross-encoder reorders the top candidates |
| Grounded answer | LLM answers only from retrieved chunks |
| Citations | UI shows which doc/page each part of the answer came from |

## Architecture

```mermaid
flowchart LR
    subgraph Ingestion
      U[Upload] --> P[Parse<br/>Tika / unstructured] --> C[Chunk] --> E[Embed] --> ES[(Elasticsearch)]
    end
    subgraph Query
      Q[Question] --> QE[Embed query]
      Q --> BM[BM25 search]
      QE --> KNN[kNN search]
      BM --> RRF[RRF merge]
      KNN --> RRF
      RRF --> RR[Reranker<br/>bge-reranker]
      RR --> LLM[Gemini<br/>answer + citations]
    end
    ES --- BM
    ES --- KNN
```

## Tools & Why

| Tool | Role in this phase | Notes |
|---|---|---|
| **Apache Tika** (`tika-python`) | Parses PDF/DOCX into text | Runs as a Docker container; alternatives: `unstructured`, `pypdf`, `python-docx`, `pymupdf` |
| **LangChain text splitters** | Chunking | `RecursiveCharacterTextSplitter`, ~500–800 tokens, 10–15% overlap |
| **Embedding model** | Text → vector | Gemini `text-embedding-004`, or `sentence-transformers` (`BAAI/bge-small-en-v1.5`, `intfloat/e5-base-v2`) |
| **Elasticsearch 8.x** | Stores chunks; BM25 + `dense_vector` kNN + RRF | Official `elasticsearch` async Python client |
| **bge-reranker** (bonus) | Cross-encoder reranking | `BAAI/bge-reranker-base` via `sentence-transformers` `CrossEncoder` |
| **FastAPI `UploadFile` + BackgroundTasks** | Upload and background ingestion | Moves to Kafka in Phase 6 |
| **Gemini API** | Answer generation | Prompt forces citing chunk IDs |

## Elasticsearch Index Mapping

```json
{
  "mappings": {
    "properties": {
      "doc_id":    { "type": "keyword" },
      "doc_name":  { "type": "keyword" },
      "page":      { "type": "integer" },
      "chunk_id":  { "type": "keyword" },
      "text":      { "type": "text", "analyzer": "english" },
      "embedding": { "type": "dense_vector", "dims": 768, "index": true, "similarity": "cosine" }
    }
  }
}
```

## Hybrid Search with RRF

Elasticsearch 8.x supports RRF with a `retriever`:

```json
{
  "retriever": {
    "rrf": {
      "retrievers": [
        { "standard": { "query": { "match": { "text": "expiry SOP for dairy" } } } },
        { "knn": { "field": "embedding", "query_vector": [/* ... */], "k": 50, "num_candidates": 200 } }
      ],
      "rank_window_size": 50,
      "rank_constant": 60
    }
  },
  "size": 20
}
```

RRF score for a document: `Σ 1 / (k + rank_i)`. If your ES license doesn't include RRF, implement it in Python (~15 lines).

## API Endpoints

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/documents` | Upload a file, returns `doc_id` |
| `GET` | `/api/documents` | List documents and status |
| `DELETE` | `/api/documents/{id}` | Delete doc and its chunks |
| `POST` | `/api/chats/{id}/messages` | Now runs RAG; SSE adds a `citations` event |
| `GET` | `/api/search?q=` | Debug endpoint: raw hybrid results with scores |

## Implementation Steps

1. Add Elasticsearch + Tika to Docker Compose; create the index.
2. Upload endpoint → save file → background ingestion task.
3. Parse → chunk (keep page numbers) → embed in batches → bulk index.
4. BM25-only search, then kNN-only, then RRF merge; compare results on a few questions.
5. Prompt template: numbered context chunks, "answer only from context, cite [n]".
6. Stream answer; send citations (doc name, page, snippet) as a separate SSE event.
7. UI: upload page, doc list, clickable citation chips.
8. Bonus: rerank top 20–50 to top 5 with bge-reranker, measure the difference.

## Key Concepts to Learn

- Why hybrid beats either alone: BM25 catches exact terms (SKU codes, IDs), vectors catch meaning.
- Chunk size/overlap trade-offs.
- Bi-encoder (embedding) vs cross-encoder (reranker).
- Hallucination control through grounding and "I don't know" answers.

## Definition of Done / Demo

- [x] Upload a PDF and see it become "ready"
- [x] Ask a question and get an answer with citations to doc + page
- [x] `/api/search` shows BM25, kNN and fused ranks
- [ ] Questions outside the docs get "I couldn't find this in your documents"

## Interview Talking Points

- How RRF works and why it doesn't need score normalization.
- Your chunking strategy and how you chose it.
- Measured impact of the reranker (fill in numbers once Phase 4 evals exist).
