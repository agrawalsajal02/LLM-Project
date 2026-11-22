"""Recursive character splitter: tries paragraph breaks first, then lines, sentences,
words, so chunks end at natural boundaries. Overlap keeps context across chunk edges."""

from app.services.parsing import Section

SEPARATORS = ["\n\n", "\n", ". ", " ", ""]


def _split(text: str, size: int, separators: list[str]) -> list[str]:
    if len(text) <= size:
        return [text]
    sep, *rest = separators
    if sep == "":
        return [text[i : i + size] for i in range(0, len(text), size)]
    pieces = text.split(sep)
    out: list[str] = []
    for i, piece in enumerate(pieces):
        piece = piece + (sep if i < len(pieces) - 1 else "")
        out.extend(_split(piece, size, rest) if len(piece) > size else [piece])
    return out


def split_text(text: str, size: int, overlap: int) -> list[str]:
    pieces = _split(text, size, SEPARATORS)
    chunks: list[str] = []
    current = ""
    for piece in pieces:
        if current and len(current) + len(piece) > size:
            chunks.append(current.strip())
            # Start the next chunk with the tail of the previous one.
            current = current[-overlap:] if overlap else ""
        current += piece
    if current.strip():
        chunks.append(current.strip())
    return [c for c in chunks if c]


def chunk_sections(sections: list[Section], size: int, overlap: int) -> list[tuple[str, int | None]]:
    """Returns (chunk_text, page) pairs. Chunks never span pages, so citations stay exact."""
    return [(chunk, s.page) for s in sections for chunk in split_text(s.text, size, overlap)]
