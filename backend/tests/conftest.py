import os

# Must be set before the app is imported: SQLite file DB + fake model, no API key or Postgres needed.
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./test.db"
os.environ["LLM_PROVIDER"] = "fake"

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app import models  # noqa: F401
from app.db import Base, engine
from app.main import app


@pytest_asyncio.fixture
async def client():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
