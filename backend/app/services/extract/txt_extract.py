from app.services.extract.base import ExtractionResult, ExtractedSection
from app.services.extract.common import ExtractionError, _headingish, clean_text


class TxtExtractor:
    def extract(self, file_path: str) -> ExtractionResult:
        try:
            with open(file_path, "r", encoding="utf-8-sig") as fh:
                content = fh.read()
        except UnicodeDecodeError:
            try:
                with open(file_path, "r", encoding="latin-1") as fh:
                    content = fh.read()
            except Exception as exc:  # noqa: BLE001
                raise ExtractionError(f"Unable to read TXT: {exc}") from exc
        except Exception as exc:  # noqa: BLE001
            raise ExtractionError(f"Unable to read TXT: {exc}") from exc
        return self.extract_text(content)

    def extract_text(self, content: str) -> ExtractionResult:
        clean = clean_text(content)
        if not clean:
            raise ExtractionError("TXT file is empty.")

        sections: list[ExtractedSection] = []
        current_section: str | None = None
        for para in clean.split("\n\n"):
            para = para.strip()
            if not para:
                continue
            lines = para.splitlines()
            first = lines[0]
            if _headingish(first):
                current_section = first[:150]
            sections.append(
                ExtractedSection(page_number=None, section=current_section, content=para)
            )

        return ExtractionResult(text=clean, sections=sections, page_count=None)