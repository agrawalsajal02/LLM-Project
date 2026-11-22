import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Conversation, Message, utcnow
from app.services.llm import ChatTurn, LLMClient

log = logging.getLogger(__name__)

TITLE_PROMPT = (
    "Write a short title (max 6 words, no quotes, no trailing punctuation) "
    "for a chat that starts with this message:\n\n{message}"
)


async def load_history(session: AsyncSession, conversation_id: uuid.UUID, limit: int) -> list[ChatTurn]:
    """Last `limit` messages, oldest first. Simple message-count window; switch to a
    token-count budget if your messages get long."""
    rows = (
        await session.scalars(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at.desc())
            .limit(limit)
        )
    ).all()
    return [ChatTurn(role=m.role, content=m.content) for m in reversed(rows)]


async def save_message(session: AsyncSession, conversation_id: uuid.UUID, role: str, content: str) -> Message:
    message = Message(conversation_id=conversation_id, role=role, content=content)
    session.add(message)
    conversation = await session.get(Conversation, conversation_id)
    if conversation is not None:
        conversation.updated_at = utcnow()
    await session.commit()
    return message


async def generate_title(llm: LLMClient, first_message: str) -> str:
    fallback = first_message.strip().splitlines()[0][:50] or "New chat"
    try:
        title = (await llm.complete(TITLE_PROMPT.format(message=first_message[:1000]))).strip().strip('"')
        return title[:200] or fallback
    except Exception:  # title is nice-to-have; never fail the chat because of it
        log.exception("title generation failed")
        return fallback
