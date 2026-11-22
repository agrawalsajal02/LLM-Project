"""Upload -> parse -> chunk -> embed -> index. Runs as a background task here; Phase 6
moves it onto a Kafka queue with workers."""

import logging
import uuid
from pathlib import Path

from fastapi import HTTPException, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app import db
from app.config import Settings, get_settings
from app.models import Document
from app.services.chunking import chunk_sections
from app.services.embeddings import get_embedder
from app.services.parsing import IMAGE_TYPES, parse_file
from app.services.search_store import Chunk, get_store

log = logging.getLogger(__name__)


def scope_for(doc: Document) -> str:
    return str(doc.conversation_id) if doc.conversation_id else "kb"


async def save_upload(
    session: AsyncSession,
    file: UploadFile,
    allowed_extensions: set[str],
    settings: Settings,
    conversation_id: uuid.UUID | None = None,
) -> Document:
    filename = Path(file.filename or "upload").name
    ext = Path(filename).suffix.lower()
    if ext not in allowed_extensions:
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            f"Unsupported file type '{ext}'. Allowed: {', '.join(sorted(allowed_extensions))}",
        )
    data = await file.read()
    if len(data) > settings.max_upload_mb * 1024 * 1024:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, f"Max upload size is {settings.max_upload_mb} MB")
    if not data:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Empty file")

    doc_id = uuid.uuid4()
    upload_dir = Path(settings.upload_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)
    path = upload_dir / f"{doc_id}{ext}"
    path.write_bytes(data)

    doc = Document(
        id=doc_id,
        filename=filename,
        content_type=IMAGE_TYPES.get(ext) or file.content_type or "application/octet-stream",
        size_bytes=len(data),
        storage_path=str(path),
        kind="attachment" if conversation_id else "kb",
        conversation_id=conversation_id,
    )
    session.add(doc)
    await session.commit()
    return doc


async def ingest_document(doc_id: uuid.UUID) -> None:
    settings = get_settings()
    async with db.SessionLocal() as session:
        doc = await session.get(Document, doc_id)
        if doc is None:
            return
        try:
            ext = Path(doc.filename).suffix.lower()
            if ext in IMAGE_TYPES:
                # Images aren't indexed; the agent sends them to the model for vision.
                doc.status = "ready"
                await session.commit()
                return

            parsed = await parse_file(Path(doc.storage_path), doc.filename, settings)
            pieces = chunk_sections(parsed.sections, settings.chunk_size, settings.chunk_overlap)
            embedder = get_embedder()
            vectors = await embedder.embed([text for text, _ in pieces], task="document")

            store = get_store()
            await store.ensure_index(embedder.dims)
            await store.delete_document(str(doc.id))  # re-ingest is idempotent
            await store.index(
                [
                    Chunk(
                        chunk_id=f"{doc.id}:{i}",
                        doc_id=str(doc.id),
                        doc_name=doc.filename,
                        scope=scope_for(doc),
                        page=page,
                        position=i,
                        text=text,
                        embedding=vector,
                    )
                    for i, ((text, page), vector) in enumerate(zip(pieces, vectors))
                ]
            )
            doc.chunk_count = len(pieces)
            doc.page_count = parsed.page_count
            doc.status = "ready"
            doc.error = None
        except Exception as exc:
            log.exception("ingestion failed for %s", doc.filename)
            doc.status = "failed"
            doc.error = str(exc)[:1000]
        await session.commit()


async def delete_document(session: AsyncSession, doc: Document) -> None:
    await get_store().delete_document(str(doc.id))
    Path(doc.storage_path).unlink(missing_ok=True)
    await session.delete(doc)
    await session.commit()
