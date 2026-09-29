from dataclasses import dataclass


@dataclass
class Chunk:
    text: str
    chunk_index: int
    page_number: int | None = None
    section: str | None = None
    row_number: int | None = None
    char_start: int | None = None
    char_end: int | None = None


def _split_ranges(total_len: int, size: int, overlap: int) -> list[tuple[int, int]]:
    ranges = []
    start = 0
    while start < total_len:
        end = min(start + size, total_len)
        ranges.append((start, end))
        if end >= total_len:
            break
        start = max(start + size - overlap, start + 1)
    return ranges


def chunk_sections(sections, chunk_size: int = 1000, overlap: int = 150) -> list[Chunk]:
    """Chunk pre-extracted sections, preserving page/section metadata per chunk."""
    if overlap >= chunk_size:
        overlap = max(chunk_size // 4, 1)

    chunks: list[Chunk] = []
    global_index = 0

    for section in sections:
        text = (section.content or "").strip()
        if not text:
            continue
        for start, end in _split_ranges(len(text), chunk_size, overlap):
            piece = text[start:end].strip()
            if not piece:
                continue
            chunks.append(
                Chunk(
                    text=piece,
                    chunk_index=global_index,
                    page_number=section.page_number,
                    section=section.section,
                    row_number=section.row_number,
                    char_start=start,
                    char_end=end,
                )
            )
            global_index += 1

    return chunks


def chunk_plain_text(text: str, chunk_size: int = 1000, overlap: int = 150) -> list[Chunk]:
    """Standalone chunking of a plain text string (used by the RAG tests)."""
    if not text.strip():
        return []
    from app.services.extract.base import ExtractedSection

    return chunk_sections([ExtractedSection(content=text)], chunk_size, overlap)