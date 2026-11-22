import asyncio
import json
import logging
import uuid
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app import db
from app.config import Settings, get_settings
from app.models import DEFAULT_TITLE, Conversation
from app.schemas import ConversationDetail, ConversationOut, ConversationUpdate, MessageIn
from app.services.chat import generate_title, load_history, save_message
from app.services.llm import LLMClient, get_llm

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/chats", tags=["chats"])


async def _get_or_404(session: AsyncSession, chat_id: uuid.UUID) -> Conversation:
    conversation = await session.get(Conversation, chat_id)
    if conversation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found")
    return conversation


@router.post("", response_model=ConversationOut, status_code=status.HTTP_201_CREATED)
async def create_chat(session: AsyncSession = Depends(db.get_session)):
    conversation = Conversation()
    session.add(conversation)
    await session.commit()
    return conversation


@router.get("", response_model=list[ConversationOut])
async def list_chats(session: AsyncSession = Depends(db.get_session)):
    rows = await session.scalars(select(Conversation).order_by(Conversation.updated_at.desc()).limit(200))
    return rows.all()


@router.get("/{chat_id}", response_model=ConversationDetail)
async def get_chat(chat_id: uuid.UUID, session: AsyncSession = Depends(db.get_session)):
    conversation = await session.scalar(
        select(Conversation).where(Conversation.id == chat_id).options(selectinload(Conversation.messages))
    )
    if conversation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found")
    return conversation


@router.patch("/{chat_id}", response_model=ConversationOut)
async def rename_chat(chat_id: uuid.UUID, body: ConversationUpdate, session: AsyncSession = Depends(db.get_session)):
    conversation = await _get_or_404(session, chat_id)
    conversation.title = body.title
    await session.commit()
    return conversation


@router.delete("/{chat_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_chat(chat_id: uuid.UUID, session: AsyncSession = Depends(db.get_session)):
    conversation = await _get_or_404(session, chat_id)
    await session.delete(conversation)
    await session.commit()


def sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


@router.post("/{chat_id}/messages")
async def send_message(
    chat_id: uuid.UUID,
    body: MessageIn,
    session: AsyncSession = Depends(db.get_session),
    settings: Settings = Depends(get_settings),
    llm: LLMClient = Depends(get_llm),
):
    """Saves the user message, then streams the assistant reply as Server-Sent Events:
    `token` {text} ... then `done` {message_id, title}, or `error` {detail}."""
    conversation = await _get_or_404(session, chat_id)
    needs_title = conversation.title == DEFAULT_TITLE
    await save_message(session, chat_id, "user", body.content)
    history = await load_history(session, chat_id, settings.history_max_messages)

    async def events() -> AsyncIterator[str]:
        parts: list[str] = []

        async def persist_reply() -> uuid.UUID | None:
            text = "".join(parts).strip()
            if not text:
                return None
            # New session: the request-scoped one may already be closed while streaming.
            async with db.SessionLocal() as s:
                return (await save_message(s, chat_id, "assistant", text)).id

        try:
            async for token in llm.stream(settings.system_prompt, history):
                parts.append(token)
                yield sse("token", {"text": token})
        except asyncio.CancelledError:
            # Client pressed "stop" or disconnected: keep what was generated so far.
            await asyncio.shield(persist_reply())
            raise
        except Exception:
            log.exception("LLM streaming failed")
            await persist_reply()
            yield sse("error", {"detail": "The model failed to respond. Please try again."})
            return

        message_id = await persist_reply()
        title = None
        if needs_title:
            title = await generate_title(llm, body.content)
            async with db.SessionLocal() as s:
                c = await s.get(Conversation, chat_id)
                if c is not None:
                    c.title = title
                    await s.commit()
        yield sse("done", {"message_id": str(message_id) if message_id else None, "title": title})

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
