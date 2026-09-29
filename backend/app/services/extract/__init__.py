from pathlib import Path

from app.services.extract.base import ExtractionResult
from app.services.extract.common import ExtractionError
from app.services.extract.csv_extract import CsvExtractor
from app.services.extract.docx_extract import DocxExtractor
from app.services.extract.pdf_extract import PdfExtractor
from app.services.extract.txt_extract import TxtExtractor

_EXTRACTORS = {
    "pdf": PdfExtractor,
    "docx": DocxExtractor,
    "csv": CsvExtractor,
    "txt": TxtExtractor,
}

SUPPORTED_FILE_TYPES = set(_EXTRACTORS.keys())


def get_extractor(file_type: str):
    cls = _EXTRACTORS.get((file_type or "").lower())
    if not cls:
        raise ExtractionError(f"Unsupported file type: {file_type}")
    return cls()


def extract_document(file_path: str | Path, file_type: str) -> ExtractionResult:
    extractor = get_extractor(file_type)
    return extractor.extract(file_path)
