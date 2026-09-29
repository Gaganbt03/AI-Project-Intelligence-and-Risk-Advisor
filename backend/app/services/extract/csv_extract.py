import csv
from io import StringIO

from app.services.extract.base import ExtractionResult, ExtractedSection
from app.services.extract.common import ExtractionError, clean_text


class CsvExtractor:
    def extract(self, file_path: str) -> ExtractionResult:
        try:
            with open(file_path, "r", encoding="utf-8-sig", newline="") as fh:
                content = fh.read()
        except UnicodeDecodeError:
            try:
                with open(file_path, "r", encoding="latin-1", newline="") as fh:
                    content = fh.read()
            except Exception as exc:  # noqa: BLE001
                raise ExtractionError(f"Unable to read CSV: {exc}") from exc
        except Exception as exc:  # noqa: BLE001
            raise ExtractionError(f"Unable to read CSV: {exc}") from exc
        return self.extract_text(content)

    def extract_text(self, content: str) -> ExtractionResult:
        try:
            reader = csv.reader(StringIO(content))
            rows = [row for row in reader if any(c.strip() for c in row)]
        except Exception as exc:  # noqa: BLE001
            raise ExtractionError(f"Unable to parse CSV: {exc}") from exc

        if not rows:
            raise ExtractionError("CSV file is empty.")

        header = [h.strip() for h in rows[0]]
        sections: list[ExtractedSection] = []
        parts: list[str] = []
        for idx, row in enumerate(rows[1:], start=2):
            pairs = []
            for col_idx, cell in enumerate(row):
                label = header[col_idx] if col_idx < len(header) else f"col{col_idx + 1}"
                val = cell.strip()
                if val:
                    pairs.append(f"{label}={val}")
            line = " | ".join(pairs)
            if not line:
                continue
            parts.append(line)
            sections.append(
                ExtractedSection(
                    page_number=None,
                    section=None,
                    content=line,
                    row_number=idx,
                )
            )

        if not sections:
            # Only a header row exists
            sections.append(
                ExtractedSection(
                    page_number=None,
                    section=None,
                    content="Header: " + " | ".join(header),
                    row_number=1,
                )
            )

        text = clean_text("\n".join(parts)) if parts else clean_text(" | ".join(header))
        return ExtractionResult(
            text=text,
            sections=sections,
            row_count=len(rows),
        )