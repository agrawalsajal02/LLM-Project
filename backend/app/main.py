import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.chats import router as chats_router
from app.api.documents import router as documents_router
from app.config import get_settings
from app.services.mcp_client import toolbox
from app.services.search_store import get_store

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger(__name__)
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await toolbox.start(settings)
    yield
    await toolbox.stop()
    await get_store().close()


app = FastAPI(title="LLM Project: Dark Store Ops Copilot", version="0.3.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(chats_router)
app.include_router(documents_router)


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "llm_provider": settings.llm_provider,
        "embedding_provider": settings.embedding_provider,
        "search_backend": settings.search_backend,
        "chat_mode": settings.chat_mode,
        "web_search": settings.web_search_provider,
        "mcp_tools": [t.name for t in toolbox.tools],
    }
