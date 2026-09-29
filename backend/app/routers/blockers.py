from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_accessible_project, get_current_user
from app.models import Blocker, User
from app.services.audit import log_event

router = APIRouter(prefix="/blockers", tags=["blockers"])


def _blocker_out(b: Blocker) -> dict:
    return {
        "id": b.id,
        "project_id": b.project_id,
        "title": b.title,
        "description": b.description,
        "severity": b.severity,
        "owner": b.owner,
        "identified_at": b.identified_at,
        "expected_resolution": b.expected_resolution,
        "status": b.status,
        "source_type": b.source_type,
        "evidence": b.evidence,
        "source_document_id": b.source_document_id,
        "source_document": b.source_document.original_name if b.source_document else "",
        "created_by": b.created_by,
        "created_at": b.created_at,
    }


@router.get("")
def list_blockers(
    project_id: int = Query(...),
    status: str | None = Query(default=None),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    get_accessible_project(project_id, user, db)
    q = db.query(Blocker).filter(Blocker.project_id == project_id)
    if status:
        q = q.filter(Blocker.status == status)
    return [_blocker_out(b) for b in q.order_by(Blocker.created_at.desc()).all()]


@router.post("", status_code=201)
def report_blocker(
    project_id: int,
    payload: dict,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Employees can report blockers on projects they belong to."""
    get_accessible_project(project_id, user, db)
    title = (payload.get("title") or "").strip()[:255]
    if not title:
        raise HTTPException(status_code=400, detail="Blocker title is required.")
    blocker = Blocker(
        project_id=project_id,
        title=title,
        description=payload.get("description") or "",
        severity=payload.get("severity") or "Medium",
        owner=payload.get("owner") or user.name,
        expected_resolution=payload.get("expected_resolution") or "",
        status="Open",
        source_type="employee_reported",
        created_by=user.id,
    )
    db.add(blocker)
    db.commit()
    db.refresh(blocker)
    log_event(db, user_id=user.id, user_email=user.email, action="blocker_created",
              resource_type="blocker", resource_id=str(blocker.id), project_id=project_id,
              detail=title)
    return _blocker_out(blocker)


@router.put("/{blocker_id}")
def update_blocker(blocker_id: int, payload: dict, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    blocker = db.query(Blocker).filter(Blocker.id == blocker_id).first()
    if not blocker:
        raise HTTPException(status_code=404, detail="Blocker not found.")
    get_accessible_project(blocker.project_id, user, db)
    for field in ("status", "severity", "owner", "expected_resolution", "description"):
        if field in payload:
            setattr(blocker, field, payload[field])
    if "title" in payload and payload["title"]:
        blocker.title = payload["title"][:255]
    db.commit()
    db.refresh(blocker)
    log_event(db, user_id=user.id, user_email=user.email, action="blocker_updated",
              resource_type="blocker", resource_id=str(blocker.id), project_id=blocker.project_id,
              detail=blocker.title)
    return _blocker_out(blocker)