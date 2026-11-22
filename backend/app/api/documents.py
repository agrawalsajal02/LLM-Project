import uuid
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import db
from app.config import Settings, get_settings
from app.models import Conversation, Document
from app.schemas import DocumentOut, SearchHitOut
from app.services.ingestion import delete_document, ingest_document, save_upload
from app.services.parsing import ATTACHMENT_EXTENSIONS, KB_EXTENSIONS
from app.services.retrieval import Retriever, get_retriever

router = APIRouter(prefix="/api", tags=["documents"])


async def _doc_or_404(session: AsyncSession, doc_id: uuid.UUID) -> Document:
    doc = await session.get(Document, doc_id)
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
    return doc


# ---------------------------------------------------------------- knowledge base (Phase 2)


@router.post("/documents", response_model=DocumentOut, status_code=status.HTTP_202_ACCEPTED)
async def upload_document(
    background: BackgroundTasks,
    file: UploadFile = File(...),
    session: AsyncSession = Depends(db.get_session),
    settings: Settings = Depends(get_settings),
):
    """Accepts the file and returns immediately with status "processing"; poll
    GET /api/documents until it becomes "ready" or "failed"."""
    doc = await save_upload(session, file, KB_EXTENSIONS, settings)
    background.add_task(ingest_document, doc.id)
    return doc


@router.get("/documents", response_model=list[DocumentOut])
async def list_documents(session: AsyncSession = Depends(db.get_session)):
    rows = await session.scalars(
        select(Document).where(Document.conversation_id.is_(None)).order_by(Document.created_at.desc())
    )
    return rows.all()


@router.get("/documents/{doc_id}", response_model=DocumentOut)
async def get_document(doc_id: uuid.UUID, session: AsyncSession = Depends(db.get_session)):
    return await _doc_or_404(session, doc_id)


@router.get("/documents/{doc_id}/file")
async def download_document(doc_id: uuid.UUID, session: AsyncSession = Depends(db.get_session)):
    """Serves the original file, so citations can link to it (PDFs open at #page=N)."""
    doc = await _doc_or_404(session, doc_id)
    if not Path(doc.storage_path).exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "File is missing from storage")
    return FileResponse(
        doc.storage_path, media_type=doc.content_type, filename=doc.filename, content_disposition_type="inline"
    )


@router.delete("/documents/{doc_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_document(doc_id: uuid.UUID, session: AsyncSession = Depends(db.get_session)):
    await delete_document(session, await _doc_or_404(session, doc_id))


@router.post("/documents/{doc_id}/promote", response_model=DocumentOut)
async def promote_attachment(
    doc_id: uuid.UUID, background: BackgroundTasks, session: AsyncSession = Depends(db.get_session)
):
    """Moves a chat attachment into the shared knowledge base and re-indexes it."""
    doc = await _doc_or_404(session, doc_id)
    if Path(doc.filename).suffix.lower() not in KB_EXTENSIONS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Only PDF, DOCX, TXT and MD files can go in the knowledge base")
    doc.conversation_id = None
    doc.kind = "kb"
    doc.status = "processing"
    await session.commit()
    background.add_task(ingest_document, doc.id)
    return doc


@router.get("/search", response_model=list[SearchHitOut])
async def search(
    q: str = Query(min_length=1),
    k: int = Query(10, ge=1, le=50),
    retriever: Retriever = Depends(get_retriever),
):
    """Debug view of hybrid search: shows BM25 rank, kNN rank and fused RRF score."""
    result = await retriever.search(q, scopes=["kb"], top_k=k)
    return [
        SearchHitOut(
            doc_id=r.hit.doc_id,
            doc_name=r.hit.doc_name,
            page=r.hit.page,
            text=r.hit.text,
            rrf_score=r.rrf_score,
            bm25_rank=r.bm25_rank,
            knn_rank=r.knn_rank,
            rerank_score=r.rerank_score,
        )
        for r in result.hits
    ]


# ---------------------------------------------------------------- chat attachments (Phase 3)


@router.post("/chats/{chat_id}/attachments", response_model=DocumentOut, status_code=status.HTTP_202_ACCEPTED)
async def upload_attachment(
    chat_id: uuid.UUID,
    background: BackgroundTasks,
    file: UploadFile = File(...),
    session: AsyncSession = Depends(db.get_session),
    settings: Settings = Depends(get_settings),
):
    """A file only this conversation can see: PDF/DOCX/TXT/MD/CSV/XLSX or an image."""
    if await session.get(Conversation, chat_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found")
    doc = await save_upload(session, file, ATTACHMENT_EXTENSIONS, settings, conversation_id=chat_id)
    background.add_task(ingest_document, doc.id)
    return doc


@router.get("/chats/{chat_id}/attachments", response_model=list[DocumentOut])
async def list_attachments(chat_id: uuid.UUID, session: AsyncSession = Depends(db.get_session)):
    rows = await session.scalars(
        select(Document).where(Document.conversation_id == chat_id).order_by(Document.created_at)
    )
    return rows.all()
