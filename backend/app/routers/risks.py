from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_accessible_project, get_current_user
from app.models import Risk, User
from app.schemas import ApiResponse
from app.services.audit import log_event

router = APIRouter(prefix="/risks", tags=["risks"])


def _risk_out(r: Risk) -> dict:
    return {
        "id": r.id,
        "project_id": r.project_id,
        "title": r.title,
        "description": r.description,
        "severity": r.severity,
        "probability": r.probability,
        "impact": r.impact,
        "evidence": r.evidence,
        "source_document_id": r.source_document_id,
        "source_document": r.source_document.original_name if r.source_document else "",
        "recommended_action": r.recommended_action,
        "status": r.status,
        "source_type": r.source_type,
        "created_at": r.created_at,
        "updated_at": r.updated_at,
    }


@router.get("")
def list_risks(
    project_id: int = Query(...),
    severity: str | None = Query(default=None),
    status: str | None = Query(default=None),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    get_accessible_project(project_id, user, db)
    q = db.query(Risk).filter(Risk.project_id == project_id)
    if severity:
        q = q.filter(Risk.severity == severity.capitalize())
    if status:
        q = q.filter(Risk.status == status)
    return [_risk_out(r) for r in q.order_by(Risk.id.desc()).all()]


@router.post("", status_code=201)
def create_risk(
    project_id: int,
    payload: dict,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    get_accessible_project(project_id, user, db)
    risk = Risk(
        project_id=project_id,
        title=(payload.get("title") or "").strip()[:255],
        description=payload.get("description") or "",
        severity=(payload.get("severity") or "Medium"),
        probability=(payload.get("probability") or "Medium"),
        impact=(payload.get("impact") or "Medium"),
        evidence=payload.get("evidence") or "",
        recommended_action=payload.get("recommended_action") or "",
        status=payload.get("status") or "Open",
        source_type="manual",
        created_by=user.id,
    )
    if not risk.title:
        raise HTTPException(status_code=400, detail="Risk title is required.")
    db.add(risk)
    db.commit()
    db.refresh(risk)
    log_event(db, user_id=user.id, user_email=user.email, action="risk_created",
              resource_type="risk", resource_id=str(risk.id), project_id=project_id,
              detail=risk.title)
    return _risk_out(risk)


@router.put("/{risk_id}")
def update_risk(risk_id: int, payload: dict, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    risk = db.query(Risk).filter(Risk.id == risk_id).first()
    if not risk:
        raise HTTPException(status_code=404, detail="Risk not found.")
    get_accessible_project(risk.project_id, user, db)
    for field in ("severity", "probability", "impact", "status", "evidence", "recommended_action", "description"):
        if field in payload:
            setattr(risk, field, payload[field])
    if "title" in payload and payload["title"]:
        risk.title = payload["title"][:255]
    db.commit()
    db.refresh(risk)
    log_event(db, user_id=user.id, user_email=user.email, action="risk_updated",
              resource_type="risk", resource_id=str(risk.id), project_id=risk.project_id,
              detail=risk.title)
    return _risk_out(risk)