from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """App configuration, read from environment variables or a .env file."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://chat:chat@localhost:5432/chat"
    cors_origins: list[str] = ["http://localhost:3000"]

    # ---- LLM (Phase 1) ----
    # "gemini" calls the real API; "fake" streams a canned reply (no key needed, used in tests).
    llm_provider: Literal["gemini", "fake"] = "gemini"
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"
    # Cheaper/faster model for small internal calls (titles, rewriting, planning, grading).
    gemini_fast_model: str = "gemini-2.5-flash-lite"
    system_prompt: str = "You are a helpful, concise assistant. Format answers in Markdown."
    history_max_messages: int = 20

    # ---- Documents + RAG (Phase 2) ----
    upload_dir: str = "./data/uploads"
    max_upload_mb: int = 25
    # "local" = pypdf/python-docx (no extra service); "tika" = Apache Tika server at tika_url.
    parser: Literal["local", "tika"] = "local"
    tika_url: str = "http://localhost:9998"
    chunk_size: int = 1500  # characters (~375 tokens)
    chunk_overlap: int = 200

    # "gemini" (free tier), "local" (sentence-transformers on CPU), "fake" (hashing, tests).
    embedding_provider: Literal["gemini", "local", "fake"] = "gemini"
    embedding_model: str = "gemini-embedding-001"
    local_embedding_model: str = "BAAI/bge-small-en-v1.5"
    embedding_dims: int = 768

    # "elasticsearch" for the real thing; "memory" = pure-Python BM25 + vectors saved to a
    # JSON file (no extra service; good for tests and very small setups).
    search_backend: Literal["elasticsearch", "memory"] = "elasticsearch"
    elasticsearch_url: str = "http://localhost:9200"
    elasticsearch_index: str = "chunks"
    memory_store_path: str = "./data/memory_store.json"

    retrieval_candidates: int = 50  # per retriever (BM25 and kNN) before fusion
    rrf_k: int = 60
    retrieval_top_k: int = 5  # chunks given to the LLM
    # "none" or "local" (cross-encoder via sentence-transformers).
    reranker: Literal["none", "local"] = "none"
    reranker_model: str = "BAAI/bge-reranker-base"
    rerank_candidates: int = 20

    # ---- Agent (Phase 3) ----
    # "agent" = planner decides (direct / docs / web / tools / attachments, multi-step);
    # "rag" = always search documents (Phase 2 behaviour).
    chat_mode: Literal["agent", "rag"] = "agent"
    agent_max_steps: int = 4
    agent_max_retries: int = 1  # re-plans when retrieved docs aren't relevant

    # Web search: "tavily" (free credits, needs key), "searxng" (self-hosted, free), "none".
    web_search_provider: Literal["tavily", "searxng", "none"] = "none"
    tavily_api_key: str = ""
    searxng_url: str = "http://localhost:8080"
    web_max_results: int = 5

    # MCP server with dark-store tools, started as a subprocess (stdio transport).
    mcp_enabled: bool = True
    mcp_server_command: str = "python"
    mcp_server_args: list[str] = ["-m", "mcp_server.darkstore"]


@lru_cache
def get_settings() -> Settings:
    return Settings()
