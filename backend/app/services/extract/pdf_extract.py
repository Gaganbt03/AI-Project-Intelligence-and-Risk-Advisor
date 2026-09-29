from pathlib import Path

from pypdf import PdfReader

from app.services.extract.base import ExtractionResult, ExtractedSection
from app.services.extract.common import ExtractionError, _headingish, clean_text


class PdfExtractor:
    def extract(self, file_path: str | Path) -> ExtractionResult:
        try:
            reader = PdfReader(file_path)
        except Exception as exc:  # noqa: BLE001 - pypdf raises various errors
            raise ExtractionError(f"Unable to open PDF: {exc}") from exc

        sections: list[ExtractedSection] = []
        raw_parts: list[str] = []
        for page_no, page in enumerate(reader.pages, start=1):
            try:
                content = page.extract_text() or ""
            except Exception as exc:  # noqa: BLE001
                raise ExtractionError(f"Failed to extract PDF page {page_no}: {exc}") from exc
            if not content.strip():
                continue
            content = clean_text(content)
            raw_parts.append(content)
            section = None
            first_line = content.splitlines()[0].strip() if content.splitlines() else ""
            if first_line and _headingish(first_line):
                section = first_line[:120]
            sections.append(ExtractedSection(page_number=page_no, section=section, content=content))

        if not raw_parts:
            raise ExtractionError("PDF contains no extractable text (scanned images are not supported yet).")

        return ExtractionResult(
            text="\n\n".join(raw_parts),
            sections=sections,
            page_count=len(reader.pages),
        )