import asyncio
import json
import logging
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app import db
from app.config import Settings, get_settings
from app.models import DEFAULT_TITLE, Conversation, Document
from app.schemas import ConversationDetail, ConversationOut, ConversationUpdate, MessageIn
from app.services.agent import AgentDeps, Attachment, run_agent
from app.services.chat import generate_title, load_history, save_message
from app.services.ingestion import delete_document
from app.services.llm import ImageInput, LLMClient, get_llm
from app.services.mcp_client import toolbox
from app.services.parsing import IMAGE_TYPES, TABLE_TYPES, parse_bytes
from app.services.retrieval import Retriever, get_retriever

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/chats", tags=["chats"])

INLINE_ATTACHMENT_CHARS = 15_000  # smaller files go into the prompt whole
MAX_IMAGES = 4


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
    # Remove this chat's attachments from storage and the search index too.
    for doc in (await session.scalars(select(Document).where(Document.conversation_id == chat_id))).all():
        await delete_document(session, doc)
    await session.delete(conversation)
    await session.commit()


def sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


async def _load_attachments(session: AsyncSession, chat_id: uuid.UUID) -> list[Attachment]:
    docs = (
        await session.scalars(
            select(Document)
            .where(Document.conversation_id == chat_id, Document.status == "ready")
            .order_by(Document.created_at)
        )
    ).all()
    out: list[Attachment] = []
    for doc in docs:
        ext = Path(doc.filename).suffix.lower()
        path = Path(doc.storage_path)
        if not path.exists():
            continue
        if ext in IMAGE_TYPES:
            if sum(1 for a in out if a.image) < MAX_IMAGES:
                out.append(Attachment(str(doc.id), doc.filename, "image",
                                      image=ImageInput(IMAGE_TYPES[ext], path.read_bytes(), doc.filename)))
            continue
        try:
            parsed = await asyncio.to_thread(parse_bytes, path.read_bytes(), doc.filename)
            text = "\n\n".join(s.text for s in parsed.sections)
        except Exception:
            text = ""
        kind = "table" if ext in TABLE_TYPES else "text"
        out.append(Attachment(str(doc.id), doc.filename, kind,
                              inline_text=text if 0 < len(text) <= INLINE_ATTACHMENT_CHARS else None))
    return out


@router.post("/{chat_id}/messages")
async def send_message(
    chat_id: uuid.UUID,
    body: MessageIn,
    session: AsyncSession = Depends(db.get_session),
    settings: Settings = Depends(get_settings),
    llm: LLMClient = Depends(get_llm),
    retriever: Retriever = Depends(get_retriever),
):
    """Saves the user message, runs the agent and streams Server-Sent Events:
    `step` {id, kind, label, status, detail} while the agent works,
    `citations` {citations: [...]} before the answer,
    `token` {text} for the answer, then `done` {message_id, title} or `error` {detail}."""
    conversation = await _get_or_404(session, chat_id)
    needs_title = conversation.title == DEFAULT_TITLE
    await save_message(session, chat_id, "user", body.content)
    history = await load_history(session, chat_id, settings.history_max_messages)
    attachments = await _load_attachments(session, chat_id)
    kb_count = await session.scalar(
        select(func.count()).select_from(Document).where(Document.conversation_id.is_(None), Document.status == "ready")
    )

    async def events() -> AsyncIterator[str]:
        queue: asyncio.Queue[tuple[str, dict] | None] = asyncio.Queue()
        parts: list[str] = []
        citations: list[dict] = []
        steps: dict[int, dict] = {}

        async def emit(event: str, data: dict) -> None:
            await queue.put((event, data))

        deps = AgentDeps(
            llm=llm, retriever=retriever, toolbox=toolbox, settings=settings, emit=emit,
            conversation_id=str(chat_id), kb_has_docs=bool(kb_count), attachments=attachments,
        )

        async def run() -> None:
            try:
                await run_agent(deps, history)
            finally:
                await queue.put(None)

        async def persist_reply() -> uuid.UUID | None:
            text = "".join(parts).strip()
            if not text:
                return None
            async with db.SessionLocal() as s:  # request session may be closed while streaming
                message = await save_message(s, chat_id, "assistant", text)
                message.citations = citations or None
                message.steps = list(steps.values()) or None
                await s.commit()
                return message.id

        task = asyncio.create_task(run())
        try:
            while (item := await queue.get()) is not None:
                event, data = item
                if event == "token":
                    parts.append(data["text"])
                elif event == "citations":
                    citations = data["citations"]
                elif event == "step":
                    steps[data["id"]] = data
                yield sse(event, data)
            await task  # re-raises agent errors
        except asyncio.CancelledError:
            # Client pressed "stop" or disconnected: stop the agent, keep the partial answer.
            task.cancel()
            await asyncio.shield(persist_reply())
            raise
        except Exception:
            log.exception("agent failed")
            await persist_reply()
            yield sse("error", {"detail": "The assistant failed to respond. Please try again."})
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
