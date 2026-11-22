"""File -> text sections (with page numbers where the format has pages)."""

import asyncio
import io
from dataclasses import dataclass
from pathlib import Path

import httpx

from app.config import Settings

IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}
TABLE_TYPES = {".csv", ".xlsx", ".xls"}
TEXT_TYPES = {".txt", ".md"}
DOC_TYPES = {".pdf", ".docx"}

KB_EXTENSIONS = DOC_TYPES | TEXT_TYPES
ATTACHMENT_EXTENSIONS = DOC_TYPES | TEXT_TYPES | TABLE_TYPES | set(IMAGE_TYPES)


class ParseError(Exception):
    pass


@dataclass
class Section:
    text: str
    page: int | None = None  # 1-based page number, None if the format has no pages


@dataclass
class Parsed:
    sections: list[Section]
    page_count: int | None = None


def _parse_pdf(data: bytes) -> Parsed:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    sections = [Section(text=(page.extract_text() or "").strip(), page=i + 1) for i, page in enumerate(reader.pages)]
    sections = [s for s in sections if s.text]
    if not sections:
        raise ParseError("No text found in PDF (scanned PDFs need OCR, not supported yet)")
    return Parsed(sections=sections, page_count=len(reader.pages))


def _parse_docx(data: bytes) -> Parsed:
    import docx

    document = docx.Document(io.BytesIO(data))
    blocks = [p.text for p in document.paragraphs if p.text.strip()]
    for table in document.tables:
        for row in table.rows:
            blocks.append(" | ".join(cell.text.strip() for cell in row.cells))
    if not blocks:
        raise ParseError("No text found in DOCX")
    return Parsed(sections=[Section(text="\n\n".join(blocks))])


def _parse_table(data: bytes, ext: str) -> Parsed:
    import pandas as pd

    frame = pd.read_csv(io.BytesIO(data)) if ext == ".csv" else pd.read_excel(io.BytesIO(data))
    # Rows as "col: value" lines so both BM25 and the LLM can read them.
    rows = [
        "; ".join(f"{col}: {val}" for col, val in row.items() if pd.notna(val))
        for row in frame.to_dict(orient="records")
    ]
    return Parsed(sections=[Section(text="\n".join(rows))])


def parse_bytes(data: bytes, filename: str) -> Parsed:
    ext = Path(filename).suffix.lower()
    if ext == ".pdf":
        return _parse_pdf(data)
    if ext == ".docx":
        return _parse_docx(data)
    if ext in TEXT_TYPES:
        return Parsed(sections=[Section(text=data.decode("utf-8", errors="replace"))])
    if ext in TABLE_TYPES:
        return _parse_table(data, ext)
    raise ParseError(f"Unsupported file type: {ext}")


async def _parse_with_tika(data: bytes, settings: Settings) -> Parsed:
    async with httpx.AsyncClient(timeout=120) as client:
        response = await client.put(f"{settings.tika_url}/tika", content=data, headers={"Accept": "text/plain"})
        response.raise_for_status()
    text = response.text.strip()
    if not text:
        raise ParseError("Tika returned no text")
    return Parsed(sections=[Section(text=text)])


async def parse_file(path: Path, filename: str, settings: Settings) -> Parsed:
    data = path.read_bytes()
    ext = Path(filename).suffix.lower()
    # PDFs keep the local parser even with Tika, because it gives per-page citations.
    if settings.parser == "tika" and ext == ".docx":
        return await _parse_with_tika(data, settings)
    return await asyncio.to_thread(parse_bytes, data, filename)
