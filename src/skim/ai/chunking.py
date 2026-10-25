"""Split extracted pages into overlapping chunks that keep their page range (FR-306)."""
from __future__ import annotations

from dataclasses import dataclass

CHUNK_CHARS = 1500
OVERLAP = 200


@dataclass
class Chunk:
    index: int
    page_from: int | None
    page_to: int | None
    text: str


def chunk_pages(pages: list[str], size: int = CHUNK_CHARS, overlap: int = OVERLAP) -> list[Chunk]:
    """Pages are 1-based. A chunk never spans more text than `size`; consecutive chunks of one page overlap by `overlap`;
    a short page stays whole and short neighbouring pages are NOT merged (a slide is a natural unit)."""
    assert size > overlap >= 0
    out: list[Chunk] = []
    single = len(pages) == 1  # DOCX: no page numbers
    for n, text in enumerate(pages, 1):
        text = (text or "").strip()
        start = 0
        while text and start < len(text):
            end = min(len(text), start + size)
            if end < len(text):  # prefer a line / sentence / word boundary near the end
                cut = max(text.rfind("\n", start + size // 2, end), text.rfind(". ", start + size // 2, end) + 1,
                          text.rfind(" ", start + size // 2, end))
                end = cut if cut > start else end
            piece = text[start:end].strip()
            if piece:
                out.append(Chunk(len(out), None if single else n, None if single else n, piece))
            if end >= len(text):
                break
            start = max(end - overlap, start + 1)
    return out
