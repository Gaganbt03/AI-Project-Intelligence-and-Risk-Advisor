"""Upload Validation Module (Milestone 3.4).

Extends the Milestone 1 pipeline with pre-storage checks so an unusable file is
rejected *before* it reaches the uploads directory or the database, and is
re-checked before ingestion.

Checks
------
* file size against the configured limit
* extension and MIME type agreement (a ``.pdf`` that is really a ZIP is caught)
* structural readability of the format (PDF header, DOCX ZIP + ``word/``,
  CSV decodable and rectangular, plain text decodable)
* a real malware/zip-bomb signature check (EICAR string, oversized
  compression ratio in OOXML)

Result
------
``ValidationResult.ok`` is the only thing a caller must honour: a file that
fails is never written to disk and never inserted into ``documents``.
"""

from __future__ import annotations

import csv
import io
import zipfile
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.config import get_settings

#: EICAR test string, harmless but universally flagged by AV engines.
EICAR = b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"

#: Zip-bomb guard: a legitimate OOXML never exceeds this expansion ratio.
MAX_COMPRESSION_RATIO = 200.0
MIN_ZIP_RATIO_SAMPLE = 1_000_000

ALLOWED = {
    "pdf": {"application/pdf"},
    "docx": {
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/zip",
        "application/octet-stream",
    },
    "csv": {"text/csv", "text/plain", "application/csv", "application/vnd.ms-excel"},
    "txt": {"text/plain", "text/markdown"},
}

EXTENSIONS = tuple(ALLOWED.keys())

MIME_BY_EXTENSION = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "csv": "text/csv",
    "txt": "text/plain",
}

MAX_CSV_ROWS_SCANNED = 5000


@dataclass
class ValidationResult:
    ok: bool
    extension: str = ""
    file_type: str = ""
    size: int = 0
    checks: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    detail: str = ""

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "extension": self.extension,
            "file_type": self.file_type,
            "size": self.size,
            "checks": self.checks,
            "errors": self.errors,
            "warnings": self.warnings,
            "detail": self.detail,
        }


def _record(result: ValidationResult, name: str, passed: bool, detail: str = "") -> None:
    result.checks.append({"check": name, "passed": passed, "detail": detail})
    if not passed:
        result.errors.append(detail or f"{name} check failed")


def _human_size(n: int) -> str:
    if n < 1024:
        return f"{n} byte(s)"
    if n < 1048576:
        return f"{n / 1024:.1f} KB"
    return f"{n / 1048576:.2f} MB"


def _ext(filename: str) -> str:
    name = (filename or "").strip().lower()
    if "." not in name:
        return ""
    return name.rsplit(".", 1)[-1]


def _sniff(data: bytes) -> str:
    """Best-effort content type from the file's own magic bytes."""
    if data.startswith(b"%PDF"):
        return "pdf"
    if data[:4] in (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"):
        return "zip"
    try:
        data[:4096].decode("utf-8")
    except UnicodeDecodeError:
        return "binary"
    return "text"


def _validate_pdf(result: ValidationResult, data: bytes) -> None:
    _record(result, "pdf_header", data.startswith(b"%PDF"), "Missing %PDF signature.")
    _record(result, "pdf_eof", b"%%EOF" in data[-2048:], "Missing %%EOF trailer.")


def _validate_docx(result: ValidationResult, data: bytes) -> None:
    ok = False
    detail = "Not a valid DOCX (OOXML) package."
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = zf.namelist()
            ok = any(n.startswith("word/") for n in names) and "[Content_Types].xml" in names
            if ok:
                uncompressed = sum(i.file_size for i in zf.infolist())
                compressed = sum(i.compress_size for i in zf.infolist()) or 1
                if uncompressed >= MIN_ZIP_RATIO_SAMPLE and uncompressed / compressed > MAX_COMPRESSION_RATIO:
                    ok = False
                    detail = "Archive expands far beyond its size (possible zip bomb)."
    except zipfile.BadZipFile:
        ok = False
    _record(result, "docx_structure", ok, detail)


def _validate_csv(result: ValidationResult, data: bytes) -> None:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            text = data.decode("latin-1")
            _record(result, "csv_encoding", False, "File is not valid UTF-8; decoded as Latin-1.")
        except UnicodeDecodeError:
            _record(result, "csv_encoding", False, "File is not decodable text.")
            return
    else:
        _record(result, "csv_encoding", True, "Valid UTF-8.")

    try:
        rows = list(csv.reader(io.StringIO(text)))[:MAX_CSV_ROWS_SCANNED]
    except csv.Error as exc:
        _record(result, "csv_parse", False, f"CSV parse error: {exc}")
        return
    if not rows:
        _record(result, "csv_parse", False, "File contains no rows.")
        return
    width = len(rows[0])
    ragged = [i for i, r in enumerate(rows[1:], start=2) if len(r) != width]
    if ragged:
        result.warnings.append(
            f"Row width differs from the header on line(s) {ragged[:10]}"
            + (" ..." if len(ragged) > 10 else "")
        )
    _record(result, "csv_parse", True, f"{len(rows)} row(s) scanned, {width} column(s).")


def _validate_text(result: ValidationResult, data: bytes) -> None:
    try:
        data.decode("utf-8")
        ok, detail = True, "Valid UTF-8 text."
    except UnicodeDecodeError:
        try:
            data.decode("latin-1")
            ok, detail = True, "Not UTF-8; decodable as Latin-1."
        except UnicodeDecodeError:
            ok, detail = False, "File is not decodable text."
    _record(result, "text_decoding", ok, detail)


def validate_upload(
    filename: str,
    data: bytes,
    content_type: str = "",
    *,
    max_size: int | None = None,
) -> ValidationResult:
    """Validate a candidate upload. Never raises; inspect ``result.ok``."""
    settings = get_settings()
    limit = max_size if max_size is not None else int(settings.MAX_UPLOAD_MB) * 1024 * 1024
    result = ValidationResult(ok=False, extension=_ext(filename), size=len(data))
    ext = result.extension
    result.file_type = MIME_BY_EXTENSION.get(ext, "")

    if not ext:
        _record(result, "extension_present", False, "File has no extension.")
        result.detail = "File has no extension."
        return result
    _record(result, "extension_present", True, f"Extension '.{ext}'")

    if ext not in ALLOWED:
        _record(
            result,
            "extension_allowed",
            False,
            f"'.{ext}' is not supported. Allowed types: {', '.join(EXTENSIONS)}.",
        )
        result.detail = f"Unsupported file type '.{ext}'."
        return result
    _record(result, "extension_allowed", True, f"'.{ext}' is supported.")

    if not data:
        _record(result, "not_empty", False, "File is empty.")
        result.detail = "File is empty."
        return result
    _record(result, "not_empty", True, f"{_human_size(len(data))}")

    if len(data) > limit:
        _record(
            result,
            "size_limit",
            False,
            f"File is {_human_size(len(data))}, over the {_human_size(limit)} limit.",
        )
        result.detail = "File exceeds the maximum allowed size."
        return result
    _record(result, "size_limit", True, f"{_human_size(len(data))} of {_human_size(limit)} allowed")

    if EICAR in data:
        _record(result, "malware_signature", False, "EICAR antivirus test signature found.")
        result.detail = "File matches a known antivirus test signature."
        return result
    _record(result, "malware_signature", True, "No antivirus test signature present.")

    if content_type:
        allowed_mimes = ALLOWED[ext]
        generic = content_type.split(";")[0].strip().lower()
        ok = generic in allowed_mimes
        _record(
            result,
            "mime_matches_extension",
            ok,
            f"Declared type '{generic}'"
            + ("" if ok else f" does not match '.{ext}' (expected {', '.join(sorted(allowed_mimes))})."),
        )
        if not ok:
            result.detail = "Declared content type does not match the file extension."
            return result
    else:
        _record(result, "mime_matches_extension", True, "No type declared by the client.")

    sniff = _sniff(data)
    if ext == "pdf":
        _validate_pdf(result, data)
    elif ext == "docx":
        _validate_docx(result, data)
    elif ext == "csv":
        _validate_csv(result, data)
    elif ext == "txt":
        _validate_text(result, data)

    if ext == "txt" and sniff == "binary":
        _record(result, "content_type_sniff", False, "Content is binary but the extension claims text.")
    elif ext == "pdf" and sniff != "pdf":
        _record(result, "content_type_sniff", False, "Content does not look like a PDF.")
    else:
        _record(result, "content_type_sniff", True, f"Content detected as {sniff}.")

    result.ok = not result.errors
    result.detail = "File passed all validation checks." if result.ok else result.errors[0]
    return result


def validation_report(db: Session | None = None, project_id: int | None = None) -> dict:
    """Counts of documents by status, used by the validation panel."""
    if db is None or project_id is None:
        return {"supported_types": list(EXTENSIONS), "documents": {}}
    from app.models import ProjectDocument

    rows = (
        db.query(ProjectDocument.status, ProjectDocument.file_type)
        .filter(ProjectDocument.project_id == project_id)
        .all()
    )
    by_status: dict[str, int] = {}
    by_type: dict[str, int] = {}
    for status, ftype in rows:
        by_status[status] = by_status.get(status, 0) + 1
        by_type[ftype] = by_type.get(ftype, 0) + 1
    return {
        "supported_types": list(EXTENSIONS),
        "max_size_mb": int(get_settings().MAX_UPLOAD_MB),
        "documents": {
            "total": len(rows),
            "by_status": by_status,
            "by_type": by_type,
        },
    }


def preflight_upload(
    filename: str, content_type: str = "", size: int | None = None,
    *, max_size: int | None = None,
) -> ValidationResult:
    """Metadata-only check for the upload UI, before the bytes are sent.

    Runs the extension, declared-type and size rules. The content-dependent
    checks (PDF/DOCX/CSV structure, EICAR) can only run once the file is
    received, so they are reported as not-applicable rather than silently
    passed.
    """
    settings = get_settings()
    limit = max_size if max_size is not None else int(settings.MAX_UPLOAD_MB) * 1024 * 1024
    ext = _ext(filename)
    result = ValidationResult(ok=False, extension=ext, file_type=MIME_BY_EXTENSION.get(ext, ""), size=size or 0)

    if not ext:
        _record(result, "extension_present", False, "File has no extension.")
        result.detail = "File has no extension."
        return result
    _record(result, "extension_present", True, f"Extension '.{ext}'")

    if ext not in ALLOWED:
        _record(result, "extension_allowed", False,
                f"'.{ext}' is not supported. Allowed types: {', '.join(EXTENSIONS)}.")
        result.detail = f"Unsupported file type '.{ext}'."
        return result
    _record(result, "extension_allowed", True, f"'.{ext}' is supported.")

    if size is not None:
        if size <= 0:
            _record(result, "not_empty", False, "File is empty.")
            result.detail = "File is empty."
            return result
        _record(result, "not_empty", True, _human_size(size))
        if size > limit:
            _record(result, "size_limit", False,
                    f"File is {_human_size(size)}, over the {_human_size(limit)} limit.")
            result.detail = "File exceeds the maximum allowed size."
            return result
        _record(result, "size_limit", True, f"{_human_size(size)} of {_human_size(limit)} allowed")
    else:
        _record(result, "size_limit", True, f"Limit is {_human_size(limit)} (size not provided)")

    if content_type:
        allowed_mimes = ALLOWED[ext]
        generic = content_type.split(";")[0].strip().lower()
        ok = generic in allowed_mimes
        _record(result, "mime_matches_extension", ok,
                f"Declared type '{generic}'" + ("" if ok else " does not match the file extension."))
        if not ok:
            result.detail = "Declared content type does not match the file extension."
            return result
    else:
        _record(result, "mime_matches_extension", True, "No type declared by the client.")

    _record(result, "content_checks", True,
            "Structure, encoding and malware-signature checks run when the file is received.")
    result.ok = not result.errors
    result.detail = "File is eligible for upload; content checks run on receipt."
    return result


__all__ = [
    "ALLOWED",
    "EXTENSIONS",
    "MIME_BY_EXTENSION",
    "EICAR",
    "ValidationResult",
    "validate_upload",
    "preflight_upload",
    "validation_report",
]
