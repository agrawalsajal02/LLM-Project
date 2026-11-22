import os
import tempfile

# Must be set before the app is imported: SQLite, in-memory search, fake model and
# embeddings, no MCP subprocess. No API keys, Postgres or Elasticsearch needed.
_tmp = tempfile.mkdtemp(prefix="llm-tests-")
os.environ.update(
    {
        "DATABASE_URL": f"sqlite+aiosqlite:///{_tmp}/test.db",
        "LLM_PROVIDER": "fake",
        "EMBEDDING_PROVIDER": "fake",
        # TEST_SEARCH_BACKEND=elasticsearch runs the same tests against a real ES at ELASTICSEARCH_URL.
        "SEARCH_BACKEND": os.environ.get("TEST_SEARCH_BACKEND", "memory"),
        "ELASTICSEARCH_INDEX": "test-chunks",
        "MEMORY_STORE_PATH": "",
        "UPLOAD_DIR": f"{_tmp}/uploads",
        "MCP_ENABLED": "false",
        "WEB_SEARCH_PROVIDER": "none",
        "CHAT_MODE": "agent",
    }
)

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app import models  # noqa: F401
from app.db import Base, engine
from app.main import app
from app.services import search_store
from app.services.mcp_client import toolbox


@pytest_asyncio.fixture
async def client():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    search_store.get_store.cache_clear()  # fresh, empty search index per test
    store = search_store.get_store()
    if isinstance(store, search_store.ElasticsearchStore):
        await store._es.indices.delete(index="test-chunks", ignore_unavailable=True)
    toolbox.tools = []
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()
    await store.close()
