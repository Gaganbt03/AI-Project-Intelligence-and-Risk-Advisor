from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Blocker, DocumentChunk, ProjectDocument, Risk, Task
from app.services.audit import log_event
from app.services.chunking import chunk_sections
from app.services.embeddings import EmbeddingError, get_embedding_provider
from app.services.extract import extract_document
from app.services.extract.common import ExtractionError
from app.services.vector_store import get_vector_store

logger = logging.getLogger("documents")

CLEANED_STATUSES = {"Uploaded", "Processing", "Processed", "Failed"}


class DocumentUploadError(Exception):
    pass


def _content_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def validate_upload(file_bytes: bytes, filename: str) -> tuple[str, str]:
    """Returns (file_type, mime_type) or raises DocumentUploadError."""
    from fastapi import HTTPException

    ext = Path(filename).suffix.lower().lstrip(".")
    allowed = {"pdf": "application/pdf", "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "csv": "text/csv", "txt": "text/plain"}
    if ext not in allowed:
        raise HTTPException(status_code=415, detail=f"Unsupported file type '.{ext}'. Supported: PDF, DOCX, CSV, TXT.")

    settings = get_settings()
    if len(file_bytes) > settings.MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds the {settings.MAX_UPLOAD_MB} MB upload limit.",
        )
    if not file_bytes:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    if ext == "pdf" and file_bytes[:4] != b"%PDF":
        raise HTTPException(status_code=400, detail="Invalid PDF file (missing PDF header).")
    if ext == "docx" and file_bytes[:2] != b"PK":
        raise HTTPException(status_code=400, detail="Invalid DOCX file (not a valid archive).")

    return ext, allowed[ext]


def store_original(project_id: int, file_bytes: bytes, original_name: str, file_type: str) -> str:
    settings = get_settings()
    project_dir = Path(settings.UPLOAD_DIR) / str(project_id)
    project_dir.mkdir(parents=True, exist_ok=True)
    stored_name = f"{uuid.uuid4().hex}.{file_type}"
    stored_path = project_dir / stored_name
    stored_path.write_bytes(file_bytes)
    return str(stored_path)


def create_document_record(
    db: Session,
    *,
    project_id: int,
    file_bytes: bytes,
    original_name: str,
    file_type: str,
    storage_path: str,
    uploaded_by: int,
    mime_type: str = "",
) -> ProjectDocument:
    # Duplicate detection within the same project
    digest = _content_hash(file_bytes)
    existing = (
        db.query(ProjectDocument)
        .filter(
            ProjectDocument.project_id == project_id,
            ProjectDocument.content_hash == digest,
            ProjectDocument.status.in_(["Processed", "Processing"]),
        )
        .first()
    )
    if existing:
        raise DocumentUploadError(
            f"Duplicate file: this content is already uploaded as '{existing.original_name}'."
        )

    doc = ProjectDocument(
        project_id=project_id,
        file_name=Path(storage_path).name,
        original_name=original_name,
        file_type=file_type,
        mime_type=mime_type,
        file_size=len(file_bytes),
        storage_path=storage_path,
        uploaded_by=uploaded_by,
        status="Uploaded",
        content_hash=digest,
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)
    return doc


def process_document(db: Session, document_id: int, *, audit_request=None) -> ProjectDocument:
    """Run the full ingestion pipeline for one document.

    Upload -> extract -> clean -> metadata -> chunk -> embeddings -> vector store.
    """
    settings = get_settings()
    doc = db.query(ProjectDocument).filter(ProjectDocument.id == document_id).first()
    if not doc:
        raise DocumentUploadError("Document not found.")

    doc.status = "Processing"
    doc.embedding_status = "Processing"
    doc.error_message = None
    db.commit()

    log_event(
        db,
        user_id=doc.uploaded_by,
        user_email="",
        action="document_processing_started",
        resource_type="document",
        resource_id=str(doc.id),
        project_id=doc.project_id,
        detail=f"{doc.original_name}",
        request=audit_request,
    )

    try:
        # 1. Text extraction
        result = extract_document(doc.storage_path, doc.file_type)
        if not result.sections:
            raise ExtractionError("No text could be extracted from the document.")

        cleaned_text = result.join_sections()
        if not cleaned_text.strip():
            raise ExtractionError("Document contained no meaningful text after cleaning.")

        doc.page_count = result.page_count
        doc.extracted_text = cleaned_text

        # 2. Metadata generation
        metadata = {
            "document_id": doc.id,
            "project_id": doc.project_id,
            "file_name": doc.original_name,
            "file_type": doc.file_type,
            "pages": result.page_count,
            "rows": result.row_count,
        }

        # 3. Chunking
        chunks = chunk_sections(
            result.sections,
            chunk_size=settings.CHUNK_SIZE,
            overlap=settings.CHUNK_OVERLAP,
        )
        if not chunks:
            raise ExtractionError("Document produced no chunks after processing.")

        # Replace stale chunks
        db.query(DocumentChunk).filter(DocumentChunk.document_id == doc.id).delete()
        db.commit()

        # 4. Embeddings
        provider = get_embedding_provider()
        try:
            embeddings = provider.embed_batch([c.text for c in chunks])
        except EmbeddingError as exc:
            raise ExtractionError(f"Embedding generation failed: {exc}") from exc

        if len(embeddings) != len(chunks):
            raise ExtractionError("Embedding count mismatch after generation.")

        # 5. Persist chunks (relational)
        for chunk, emb in zip(chunks, embeddings):
            db.add(
                DocumentChunk(
                    document_id=doc.id,
                    project_id=doc.project_id,
                    chunk_index=chunk.chunk_index,
                    text=chunk.text,
                    page_number=chunk.page_number,
                    section=chunk.section,
                    row_number=chunk.row_number,
                    char_start=chunk.char_start,
                    char_end=chunk.char_end,
                )
            )
        db.flush()

        # 6. Index into vector store (use the in-memory chunks with full metadata)
        vs = get_vector_store()
        added = vs.add_document_chunks(
            project_id=doc.project_id,
            document_id=doc.id,
            original_name=doc.original_name,
            chunks=chunks,
            embeddings=embeddings,
        )

        doc.chunk_count = len(chunks)
        doc.embedding_status = "Completed"
        doc.status = "Processed"
        doc.updated_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(doc)

        logger.info(
            "Processed document %s (%d chunks, %d vectors). metadata=%s",
            doc.original_name, doc.chunk_count, added, metadata,
        )
    except (ExtractionError, EmbeddingError, DocumentUploadError) as exc:
        doc.status = "Failed"
        doc.embedding_status = "Failed"
        doc.error_message = str(exc)[:2000]
        doc.updated_at = datetime.now(timezone.utc)
        db.commit()

        log_event(
            db,
            user_id=doc.uploaded_by,
            user_email="",
            action="document_processing_failed",
            resource_type="document",
            resource_id=str(doc.id),
            project_id=doc.project_id,
            detail=f"{doc.original_name}: {exc}",
            request=audit_request,
        )
        logger.warning("Document %s failed: %s", doc.original_name, exc)

    return doc


def reprocess_document(db: Session, document_id: int, *, audit_request=None) -> ProjectDocument:
    vs = get_vector_store()
    doc = db.query(ProjectDocument).filter(ProjectDocument.id == document_id).first()
    if not doc:
        raise DocumentUploadError("Document not found.")
    vs.delete_document(doc.project_id, doc.id)
    db.query(DocumentChunk).filter(DocumentChunk.document_id == doc.id).delete()
    db.commit()
    return process_document(db, document_id, audit_request=audit_request)


def process_and_analyze(
    db: Session,
    document_id: int,
    *,
    audit_request=None,
    created_by: int | None = None,
    run_analysis: bool = True,
    reprocess: bool = False,
) -> ProjectDocument:
    """Ingest one document, then automatically analyse the whole project.

    This is the single entry point the upload and reprocess routes schedule. It
    keeps ingestion and analysis in order: the agents, the deterministic health
    score and the generated documents all read from the chunks and vectors that
    ingestion just wrote, so they are grounded in the new upload.

    The assistant is deliberately *not* touched here. Its RAG context is warm
    once the document is indexed; a question, conversation or message is only
    ever produced by an explicit user request through the assistant endpoints.
    """
    doc = reprocess_document(db, document_id, audit_request=audit_request) if reprocess else (
        process_document(db, document_id, audit_request=audit_request)
    )
    if not run_analysis or doc.status != "Processed":
        return doc

    from app.services.pipeline import run_project_pipeline

    try:
        run_project_pipeline(
            db,
            doc.project_id,
            document_id=doc.id,
            created_by=created_by if created_by is not None else doc.uploaded_by,
            trigger="document_reprocess" if reprocess else "document_upload",
            audit_request=audit_request,
        )
    except Exception:  # noqa: BLE001 - the document itself is already processed
        logger.exception(
            "Automatic analysis failed for document %s (project %s). "
            "The document stays processed and can be re-analysed later.",
            document_id, doc.project_id,
        )
        db.rollback()
    return doc


def delete_document(
    db: Session,
    document_id: int,
    *,
    audit_request=None,
    user_id: int | None = None,
    user_email: str = "",
) -> None:
    vs = get_vector_store()
    doc = db.query(ProjectDocument).filter(ProjectDocument.id == document_id).first()
    if not doc:
        return

    project_id = doc.project_id
    original_name = doc.original_name
    storage_path = doc.storage_path
    vs.delete_document(project_id, document_id)
    for model in (Risk, Blocker, Task):
        db.query(model).filter(model.source_document_id == document_id).update(
            {model.source_document_id: None}, synchronize_session=False
        )
    db.query(DocumentChunk).filter(DocumentChunk.document_id == document_id).delete(
        synchronize_session=False
    )
    db.delete(doc)
    db.commit()

    try:
        Path(storage_path).unlink(missing_ok=True)
    except OSError as exc:
        logger.warning("Could not remove stored document %s: %s", storage_path, exc)

    log_event(
        db,
        user_id=user_id,
        user_email=user_email,
        action="document_deleted",
        resource_type="document",
        resource_id=str(document_id),
        project_id=project_id,
        detail=original_name,
        request=audit_request,
    )