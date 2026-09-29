from io import StringIO

from app.services.extract.base import ExtractionResult, ExtractedSection


class ExtractionError(Exception):
    pass


def clean_text(text: str) -> str:
    """Normalize whitespace/newlines for storage and chunking."""
    lines = []
    for raw in text.splitlines():
        line = raw.strip()
        if line:
            lines.append(line)
        elif lines and lines[-1] != "":
            lines.append("")
    cleaned = "\n".join(lines)
    # Collapse 3+ blank lines to 2
    while "\n\n\n" in cleaned:
        cleaned = cleaned.replace("\n\n\n", "\n\n")
    return cleaned.strip()


def extract_from_buffer(buf: StringIO, file_type: str) -> ExtractionResult:
    """Extract text from an in-memory buffer (used by tests)."""
    from app.services.extract.csv_extract import CsvExtractor
    from app.services.extract.docx_extract import (  # noqa: F401
        DocxExtractor,  # needs bytes, not StringIO
    )
    from app.services.extract.pdf_extract import (  # noqa: F401
        PdfExtractor,
    )
    from app.services.extract.txt_extract import TxtExtractor

    content = buf.getvalue()
    if file_type == "txt":
        return TxtExtractor().extract_text(content)
    if file_type == "csv":
        return CsvExtractor().extract_text(content)
    raise ExtractionError(f"Buffer extraction not supported for {file_type}")


def _headingish(line: str, max_len: int = 90) -> bool:
    if not line:
        return False
    if len(line) > max_len:
        return False
    if line[-1] in ".?:!,":
        return False
    if line.isupper() and len(line) >= 3:
        return True
    # common heading patterns
    lowered = line.lower()
    for prefix in ("chapter ", "section ", "phase ", "milestone ", "objective ", "goal ", "scope ", "introduction", "overview", "deliverable", "risks", "schedule", "timeline", "meeting ", "minutes", "agenda", "decisions", "action items", "dependencies"):
        if lowered.startswith(prefix):
            return True
    return False