import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ConversationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    created_at: datetime
    updated_at: datetime


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    role: Literal["user", "assistant"]
    content: str
    citations: list[dict] | None = None
    steps: list[dict] | None = None
    created_at: datetime


class ConversationDetail(ConversationOut):
    messages: list[MessageOut]


class ConversationUpdate(BaseModel):
    title: str = Field(min_length=1, max_length=200)


class MessageIn(BaseModel):
    content: str = Field(min_length=1, max_length=20_000)


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    filename: str
    content_type: str
    size_bytes: int
    kind: str
    conversation_id: uuid.UUID | None
    status: str
    error: str | None
    chunk_count: int
    page_count: int | None
    created_at: datetime


class SearchHitOut(BaseModel):
    doc_id: str
    doc_name: str
    page: int | None
    text: str
    rrf_score: float
    bm25_rank: int | None
    knn_rank: int | None
    rerank_score: float | None
