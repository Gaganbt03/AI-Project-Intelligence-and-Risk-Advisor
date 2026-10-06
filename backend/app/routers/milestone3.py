"""Milestone 3 API — documentation, health, conversational assistant, validation.

Every route is project-scoped and goes through ``get_accessible_project`` (or an
equivalent inline membership check), so a user can never reach another project's
documents, generated documents, health history or conversations. State-changing
routes write an ``audit_logs`` row, reusing the Milestone 1/2 helper.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_accessible_project, get_current_user
from app.models import Project, User
from app.models_m3 import GeneratedDocument
from app.services.audit import log_event
from app.services.milestone3.assistant import service as assistant_service
from app.services.milestone3.common import DOC_TYPES, doc_file_name
from app.services.milestone3.documentation import (
    DocumentationError,
    generate_all,
    generate_document as generate_doc,
    get_document,
    list_documents,
    regenerate,
    summarize,
)
from app.services.milestone3.documentation.docx_export import build_docx
from app.services.milestone3.health import (
    FORMULA_TEXT,
    snapshot_from_row,
)
from app.services.milestone3.health.service import (
    get_health,
    history as health_history,
    latest_snapshot,
    record_audit,
)
from app.services.milestone3.validation import preflight_upload
from app.services.milestone3.validation import validation_report
from app.services.pipeline import pipeline_status, run_project_pipeline

logger = logging.getLogger("m3.api")

router = APIRouter(prefix="/projects/{project_id}", tags=["project intelligence"])


# --------------------------------------------------------------------------- #
# Request models
# --------------------------------------------------------------------------- #


class GenerateRequest(BaseModel):
    doc_type: str | None = Field(default=None, description="Omit to generate all document types.")


class AskM3Request(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    conversation_id: int | None = None
    persist: bool = True

    @field_validator("question")
    @classmethod
    def _non_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("question must not be blank")
        return v


class ConversationRequest(BaseModel):
    title: str = Field(default="", max_length=255)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _require_ai_access(project_id: int, user: User, db: Session) -> None:
    """Access guard retained as a no-op passthrough (roles/membership removed)."""
    # Milestone 3 features remain available to every authenticated user.
    return


def _doc_out(doc: GeneratedDocument) -> dict:
    return {
        "id": doc.id,
        "project_id": doc.project_id,
        "doc_type": doc.doc_type,
        "title": doc.title,
        "content": doc.content,
        "payload": doc.payload,
        "file_name": doc.file_name,
        "file_type": doc.file_type,
        "generation_count": doc.generation_count,
        "insufficient_evidence": doc.insufficient_evidence,
        "provider": doc.provider,
        "model": doc.model,
        "error": doc.error,
        "created_by": doc.created_by,
        "created_at": doc.created_at,
        "updated_at": doc.updated_at,
    }


def _doc_meta(doc: GeneratedDocument) -> dict:
    """List view — omits the full Markdown body."""
    out = _doc_out(doc)
    out.pop("content", None)
    out.pop("payload", None)
    return out


DOCX_MEDIA_TYPE = ("application/vnd.openxmlformats-officedocument"
                   ".wordprocessingml.document")


def _docx_response(project_id: int, doc_id: int, request: Request, user: User,
                   db: Session, *, disposition: str = "attachment") -> Response:
    """Render one stored payload as DOCX.

    Shares the lookup, authorization and audit path with the download route; the
    only difference is the ``Content-Disposition`` mode.
    """
    get_accessible_project(project_id, user, db)
    doc = get_document(db, project_id, doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Generated document not found.")

    payload = doc.payload or {}
    if not payload:
        raise HTTPException(
            status_code=409,
            detail="This document has no stored payload to export. Regenerate it first.",
        )
    try:
        content = build_docx(doc.doc_type, payload, payload.get("project", ""))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    log_event(db, user_id=user.id, user_email=user.email, action="generated_document_downloaded",
              resource_type="generated_document", resource_id=str(doc.id),
              project_id=project_id, detail=f"{doc.doc_type}:docx:{disposition}", request=request)

    file_name = doc_file_name(payload.get("project", ""), doc.doc_type, "docx")
    return Response(
        content=content,
        media_type=DOCX_MEDIA_TYPE,
        headers={"Content-Disposition": f'{disposition}; filename="{file_name}"'},
    )


# --------------------------------------------------------------------------- #
# Automatic project analysis pipeline
# --------------------------------------------------------------------------- #


@router.get("/analysis/status")
def analysis_status(project_id: int, user: User = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    """Progress of the automatic post-upload analysis, plus live record counts.

    This is a pure read. It reports what actually exists in the database, so the
    frontend can render honest progress without ever guessing.
    """
    get_accessible_project(project_id, user, db)
    return pipeline_status(db, project_id)


@router.post("/analysis/run")
def analysis_run(project_id: int, request: Request, user: User = Depends(get_current_user),
                 db: Session = Depends(get_db)):
    """Re-run the automatic pipeline on demand.

    Uploads trigger this automatically; this route only exists so a user can
    re-analyse after adding documents through another path, or retry after an
    AI provider outage. It is idempotent — records are replaced, never
    duplicated.
    """
    get_accessible_project(project_id, user, db)
    _require_ai_access(project_id, user, db)
    try:
        return run_project_pipeline(
            db, project_id, created_by=user.id, trigger="manual", audit_request=request
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# --------------------------------------------------------------------------- #
# 3.1 Documentation Generation
# --------------------------------------------------------------------------- #


@router.get("/documents")
def list_generated(project_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    get_accessible_project(project_id, user, db)
    docs = list_documents(db, project_id)
    return {
        "project_id": project_id,
        "summary": summarize(db, project_id),
        "documents": [_doc_meta(d) for d in docs],
    }


@router.get("/documents/{doc_id}")
def get_generated(project_id: int, doc_id: int, user: User = Depends(get_current_user),
                  db: Session = Depends(get_db)):
    get_accessible_project(project_id, user, db)
    doc = get_document(db, project_id, doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Generated document not found.")
    return _doc_out(doc)


@router.post("/documents/generate", status_code=201)
def generate(project_id: int, request: Request, payload: GenerateRequest = GenerateRequest(),
             user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Generate one or all documentation types. Requires project AI access."""
    get_accessible_project(project_id, user, db)
    _require_ai_access(project_id, user, db)

    if payload.doc_type and payload.doc_type not in DOC_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown doc_type '{payload.doc_type}'. Expected one of {', '.join(DOC_TYPES)}.",
        )

    if payload.doc_type:
        try:
            doc = generate_doc(db, db.query(Project).filter(Project.id == project_id).first(),
                               payload.doc_type, created_by=user.id, audit_request=request)
        except DocumentationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"generated": [_doc_meta(doc)], "summary": summarize(db, project_id)}

    project = db.query(Project).filter(Project.id == project_id).first()
    docs = generate_all(db, project, created_by=user.id, audit_request=request)
    return {
        "generated": [_doc_meta(d) for d in docs],
        "failed": len(DOC_TYPES) - len(docs),
        "summary": summarize(db, project_id),
    }


@router.post("/documents/{doc_id}/regenerate")
def regenerate_document(project_id: int, doc_id: int, request: Request,
                        user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Regenerate a generated document IN PLACE. Uploaded originals are untouched."""
    get_accessible_project(project_id, user, db)
    _require_ai_access(project_id, user, db)
    doc = get_document(db, project_id, doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Generated document not found.")
    try:
        updated = regenerate(db, doc, created_by=user.id, audit_request=request)
    except DocumentationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _doc_out(updated)


@router.get("/documents/{doc_id}/download")
def download_generated(project_id: int, doc_id: int, request: Request,
                       format: str = Query(default="md", pattern="^(md|markdown|docx)$"),
                       user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Download a generated document as Markdown (default) or DOCX.

    Both formats are rendered from the single stored ``payload``, so a DOCX can
    never disagree with the Markdown that is already in the database. Audited
    like an original download.
    """
    if format == "docx":
        return _docx_response(project_id, doc_id, request, user, db)

    get_accessible_project(project_id, user, db)
    doc = get_document(db, project_id, doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Generated document not found.")
    log_event(db, user_id=user.id, user_email=user.email, action="generated_document_downloaded",
              resource_type="generated_document", resource_id=str(doc.id),
              project_id=project_id, detail=f"{doc.doc_type}:md", request=request)
    return Response(
        content=doc.content,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{doc.file_name}"'},
    )


@router.get("/documents/{doc_id}/validation")
def document_validation(project_id: int, doc_id: int,
                        user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """The validation + correction report for one generated document.

    Returns the per-field verdicts, the document-level summary and the
    explanatory notes, so the UI can show traceability without re-deriving it.
    """
    get_accessible_project(project_id, user, db)
    doc = get_document(db, project_id, doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Generated document not found.")
    payload = doc.payload or {}
    validation = payload.get("validation") or {}
    return {
        "document_id": doc.id,
        "doc_type": doc.doc_type,
        "title": doc.title,
        "generated_at": doc.updated_at,
        "summary": {
            "status": validation.get("status", ""),
            "fields_checked": validation.get("fields_checked", 0),
            "supported": validation.get("supported", 0),
            "corrected": validation.get("corrected", 0),
            "unsupported_removed": validation.get("unsupported_removed", 0),
            "not_specified": validation.get("not_specified", 0),
            "conflicts": validation.get("conflicts", 0),
        },
        "findings": validation.get("findings", []),
        "notes": payload.get("validation_notes", []),
    }


@router.get("/documents/{doc_id}/preview.docx")
def preview_docx(project_id: int, doc_id: int, request: Request,
                 user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Stream the DOCX inline instead of as an attachment, for the in-app preview."""
    return _docx_response(project_id, doc_id, request, user, db, disposition="inline")


# --------------------------------------------------------------------------- #
# 3.2 Project Health Scoring
# --------------------------------------------------------------------------- #


@router.get("/health")
def health(project_id: int, persist: bool = Query(default=True),
           explain: bool = Query(default=True), request: Request = None,
           user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Deterministic health score. The LLM only writes the explanation prose."""
    project = get_accessible_project(project_id, user, db)
    report = get_health(db, project, persist=persist, explain=explain, created_by=user.id)
    if persist:
        record_audit(db, project_id, user.id, f"health={report['status']}", request=request)
    return report


@router.get("/health/latest")
def health_latest(project_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    get_accessible_project(project_id, user, db)
    snap = latest_snapshot(db, project_id)
    if not snap:
        raise HTTPException(status_code=404, detail="No health snapshot recorded yet.")
    return snapshot_from_row(snap)


@router.get("/health/history")
def health_history_route(project_id: int, limit: int = Query(default=20, ge=1, le=100),
                         user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    get_accessible_project(project_id, user, db)
    return {"project_id": project_id, "history": [snapshot_from_row(s) for s in health_history(db, project_id, limit)]}


@router.get("/health/formula")
def health_formula(project_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """The published formula, so the score is never a black box."""
    get_accessible_project(project_id, user, db)
    return FORMULA_TEXT


# --------------------------------------------------------------------------- #
# 3.3 Conversational Assistant
# --------------------------------------------------------------------------- #


@router.get("/assistant/conversations")
def list_conversations(project_id: int, user: User = Depends(get_current_user),
                       db: Session = Depends(get_db)):
    get_accessible_project(project_id, user, db)
    _require_ai_access(project_id, user, db)
    convs = assistant_service.list_conversations(db, project_id, user.id)
    return {"conversations": [assistant_service.conversation_to_dict(c, include_messages=False) for c in convs]}


@router.post("/assistant/conversations", status_code=201)
def create_conversation(project_id: int, payload: ConversationRequest = ConversationRequest(),
                        user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    get_accessible_project(project_id, user, db)
    _require_ai_access(project_id, user, db)
    conv = assistant_service.create_conversation(db,
                                                 db.query(Project).filter(Project.id == project_id).first(),
                                                 user.id, title=payload.title)
    return assistant_service.conversation_to_dict(conv)


@router.get("/assistant/conversations/{conversation_id}")
def get_conversation(project_id: int, conversation_id: int, user: User = Depends(get_current_user),
                     db: Session = Depends(get_db)):
    get_accessible_project(project_id, user, db)
    _require_ai_access(project_id, user, db)
    conv = assistant_service.get_conversation(db, project_id, conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    return assistant_service.conversation_to_dict(conv)


@router.delete("/assistant/conversations/{conversation_id}")
def delete_conversation(project_id: int, conversation_id: int, request: Request,
                        user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    get_accessible_project(project_id, user, db)
    _require_ai_access(project_id, user, db)
    if not assistant_service.delete_conversation(db, project_id, conversation_id):
        raise HTTPException(status_code=404, detail="Conversation not found.")
    log_event(db, user_id=user.id, user_email=user.email, action="assistant_conversation_deleted",
              resource_type="assistant_conversation", resource_id=str(conversation_id),
              project_id=project_id, detail="", request=request)
    return {"ok": True}


@router.post("/assistant/ask")
def ask(project_id: int, payload: AskM3Request, request: Request,
        user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Conversational, project-grounded Q&A with optional conversation memory."""
    get_accessible_project(project_id, user, db)
    _require_ai_access(project_id, user, db)

    project = db.query(Project).filter(Project.id == project_id).first()
    conv = None
    if payload.conversation_id is not None:
        conv = assistant_service.get_conversation(db, project_id, payload.conversation_id)
        if not conv:
            raise HTTPException(status_code=404, detail="Conversation not found.")
    elif payload.persist:
        conv = assistant_service.create_conversation(db, project, user.id, title=payload.question[:80])

    result = assistant_service.answer(db, project, payload.question, conversation=conv,
                                      user_id=user.id, persist=payload.persist)
    log_event(db, user_id=user.id, user_email=user.email, action="assistant_question_asked",
              resource_type="project", resource_id=str(project_id), project_id=project_id,
              detail=payload.question[:200], request=request)
    return result


# --------------------------------------------------------------------------- #
# 3.4 Upload validation
# --------------------------------------------------------------------------- #


@router.get("/validation/report")
def validation(project_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    get_accessible_project(project_id, user, db)
    return validation_report(db, project_id)


@router.post("/validation/check")
def validation_check(project_id: int, file_name: str = Query(...),
                     content_type: str = Query(default=""),
                     size: int | None = Query(default=None, ge=0),
                     user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Dry-run the metadata rules against a file the browser has not sent yet.

    Used by the upload UI to preview the verdict before transfer. Content
    checks (structure, encoding, malware signature) run when the file arrives.
    """
    get_accessible_project(project_id, user, db)
    result = preflight_upload(file_name, content_type, size)
    return {**result.to_dict(), "preview_only": True}


__all__ = ["router"]
