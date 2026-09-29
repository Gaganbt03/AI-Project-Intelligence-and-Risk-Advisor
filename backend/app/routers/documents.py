from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_accessible_document, get_accessible_project, get_current_user
from app.models import Project, ProjectDocument, User
from app.schemas import DocumentOut
from app.services.audit import log_event
from app.services.documents import (
    DocumentUploadError,
    create_document_record,
    delete_document,
    process_document,
    reprocess_document,
    store_original,
    validate_upload,
)

router = APIRouter(prefix="/documents", tags=["documents"])


def _doc_out(db: Session, doc: ProjectDocument) -> DocumentOut:
    project = db.query(Project).filter(Project.id == doc.project_id).first()
    uploader = db.query(User).filter(User.id == doc.uploaded_by).first() if doc.uploaded_by else None
    return DocumentOut(
        id=doc.id,
        project_id=doc.project_id,
        project_name=project.name if project else "",
        file_name=doc.file_name,
        original_name=doc.original_name,
        file_type=doc.file_type,
        file_size=doc.file_size,
        uploaded_by=doc.uploaded_by,
        uploader_name=uploader.name if uploader else "",
        uploaded_at=doc.uploaded_at,
        status=doc.status,
        error_message=doc.error_message,
        page_count=doc.page_count,
        chunk_count=doc.chunk_count,
        embedding_status=doc.embedding_status,
    )


@router.get("", response_model=list[DocumentOut])
def list_documents(
    project_id: int | None = Query(default=None),
    file_type: str | None = Query(default=None),
    status: str | None = Query(default=None),
    uploaded_by: int | None = Query(default=None),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    q = db.query(ProjectDocument)
    if user.role_code != "ADMIN":
        ids = [m.project_id for m in user.memberships]
        q = q.filter(ProjectDocument.project_id.in_(ids or [-1]))
    if project_id is not None:
        get_accessible_project(project_id, user, db)  # permission guard
        q = q.filter(ProjectDocument.project_id == project_id)
    if file_type:
        q = q.filter(ProjectDocument.file_type == file_type.lower())
    if status:
        q = q.filter(ProjectDocument.status == status)
    if uploaded_by:
        q = q.filter(ProjectDocument.uploaded_by == uploaded_by)
    docs = q.order_by(ProjectDocument.uploaded_at.desc()).all()
    return [_doc_out(db, d) for d in docs]


@router.post("/upload", response_model=DocumentOut, status_code=201)
async def upload_document(
    background_tasks: BackgroundTasks,
    project_id: int = Query(...),
    file: UploadFile = ...,
    user: User = Depends(get_current_user),
    request: Request = None,
    db: Session = Depends(get_db),
):
    get_accessible_project(project_id, user, db)

    data = await file.read()
    try:
        file_type, mime = validate_upload(data, file.filename or "")
    except HTTPException as exc:
        raise exc

    storage_path = store_original(project_id, data, file.filename or "document", file_type)
    doc = create_document_record(
        db, project_id=project_id, file_bytes=data,
        original_name=file.filename or "document",
        file_type=file_type, storage_path=storage_path,
        uploaded_by=user.id, mime_type=mime,
    )
    log_event(db, user_id=user.id, user_email=user.email, action="document_uploaded",
              resource_type="document", resource_id=str(doc.id), project_id=project_id,
              detail=f"{doc.original_name}", request=request)

    background_tasks.add_task(process_document, db, doc.id)
    db.refresh(doc)
    return _doc_out(db, doc)


@router.get("/{document_id}", response_model=DocumentOut)
def get_document(document_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    doc = get_accessible_document(document_id, user, db)
    return _doc_out(db, doc)


@router.get("/{document_id}/preview")
def preview_document(document_id: int, max_chars: int = 8000, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    doc = get_accessible_document(document_id, user, db)
    text = (doc.extracted_text or "")[:max_chars]
    return {
        "id": doc.id,
        "original_name": doc.original_name,
        "status": doc.status,
        "text": text,
        "chunk_count": doc.chunk_count,
        "page_count": doc.page_count,
        "embedding_status": doc.embedding_status,
        "error_message": doc.error_message,
        "has_more": bool(doc.extracted_text and len(doc.extracted_text) > max_chars),
    }


@router.get("/{document_id}/download")
def download_document(document_id: int, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Return the ORIGINAL uploaded file, never a regenerated copy."""
    doc = get_accessible_document(document_id, user, db)
    log_event(db, user_id=user.id, user_email=user.email, action="document_downloaded",
              resource_type="document", resource_id=str(doc.id), project_id=doc.project_id,
              detail=doc.original_name, request=request)
    return FileResponse(
        path=doc.storage_path,
        filename=doc.original_name,
        media_type=doc.mime_type or "application/octet-stream",
    )


@router.post("/{document_id}/reprocess", response_model=DocumentOut)
def reprocess(document_id: int, background_tasks: BackgroundTasks, request: Request = None,
              user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Reprocess: recompute chunks, embeddings, vectors for an existing document."""
    doc = get_accessible_document(document_id, user, db)
    if user.role_code != "ADMIN":
        raise HTTPException(status_code=403, detail="Only administrators can reprocess documents.")
    log_event(db, user_id=user.id, user_email=user.email, action="document_reprocess_requested",
              resource_type="document", resource_id=str(doc.id), project_id=doc.project_id,
              detail=doc.original_name, request=request)
    background_tasks.add_task(reprocess_document, db, document_id)
    return _doc_out(db, doc)


@router.delete("/{document_id}")
def delete(document_id: int, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    doc = get_accessible_document(document_id, user, db)
    if user.role_code != "ADMIN":
        raise HTTPException(status_code=403, detail="Only administrators can delete documents.")
    delete_document(
        db,
        document_id,
        audit_request=request,
        user_id=user.id,
        user_email=user.email,
    )
    return {"ok": True, "message": "Document deleted."}