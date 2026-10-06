from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_accessible_project, get_current_user
from app.models import AiRun, ProjectInsight, User
from app.services.agents.orchestrator import analyze_project

router = APIRouter(prefix="/projects/{project_id}/insights", tags=["insights"])


def _insight_out(i: ProjectInsight) -> dict:
    return {
        "id": i.id,
        "agent": i.agent,
        "category": i.category,
        "title": i.title,
        "summary": i.summary,
        "payload": i.payload or {},
        "evidence": i.evidence or [],
        "ai_run_id": i.ai_run_id,
        "created_at": i.created_at,
    }


@router.get("")
def list_insights(project_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    get_accessible_project(project_id, user, db)
    rows = (
        db.query(ProjectInsight)
        .filter(ProjectInsight.project_id == project_id)
        .order_by(ProjectInsight.id.desc())
        .all()
    )
    return [_insight_out(i) for i in rows]


@router.get("/health")
def project_health(project_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Underlying metrics that a future health scoring service will combine transparently."""
    get_accessible_project(project_id, user, db)
    row = (
        db.query(ProjectInsight)
        .filter(ProjectInsight.project_id == project_id, ProjectInsight.agent == "health")
        .order_by(ProjectInsight.id.desc())
        .first()
    )
    if not row:
        return {
            "has_analysis": False,
            "metrics": {
                "task_completion_rate": 0.0, "schedule_status": "Unknown", "documents": 0,
                "open_risks": 0, "open_blockers": 0, "total_tasks": 0,
            },
        }
    return {"has_analysis": True, "metrics": row.payload or {}}


@router.post("")
def full_analysis(project_id: int, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Run Project Intelligence Analysis: all five agents over the project knowledge base."""
    project = get_accessible_project(project_id, user, db)
    result = analyze_project(db, project_id, created_by=user.id, audit_request=request)
    return result


@router.get("/runs")
def ai_runs(project_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    get_accessible_project(project_id, user, db)
    runs = (
        db.query(AiRun)
        .filter(AiRun.project_id == project_id)
        .order_by(AiRun.id.desc())
        .limit(50)
        .all()
    )
    return [
        {
            "id": r.id,
            "agent": r.agent,
            "provider": r.provider,
            "model": r.model,
            "status": r.status,
            "error": r.error,
            "started_at": r.started_at,
            "ended_at": r.ended_at,
            "source_document_ids": r.source_document_ids or [],
        }
        for r in runs
    ]