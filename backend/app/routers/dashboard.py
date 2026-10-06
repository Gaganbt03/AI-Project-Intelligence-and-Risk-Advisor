from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user
from app.models import (
    AuditLog,
    Blocker,
    Project,
    ProjectDocument,
    ProjectInsight,
    Risk,
    Task,
    User,
)
from app.services.projects import project_out

router = APIRouter(tags=["dashboard"])


def _risk_level_from_metrics(metrics: dict | None, open_risks: int) -> str:
    if not metrics:
        if open_risks > 0:
            return "Unknown"
        return "No analysis"
    schedule = (metrics.get("schedule_status") or "").lower()
    if "at risk" in schedule or "significant" in schedule:
        return "High"
    if "minor" in schedule:
        return "Medium"
    return "Low"


@router.get("/dashboard")
def dashboard(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Single dashboard for every authenticated user.

    Replaces the previous split between ``/admin/dashboard`` and
    ``/me/dashboard``. The payload is the union of both so the frontend keeps one
    data source: portfolio-wide summary numbers, per-project health rows, the
    signed-in user's own tasks, and recent activity. Nothing here is derived
    from a user role.
    """
    projects = db.query(Project).order_by(Project.created_at.desc()).all()
    project_ids = [p.id for p in projects]
    scope = project_ids or [-1]

    total_active = db.query(Project).filter(Project.status == "Active").count()

    def count(model):
        return db.query(func.count(model.id)).filter(model.project_id.in_(scope)).scalar() or 0

    def status_count(model, statuses):
        return (
            db.query(func.count(model.id))
            .filter(model.project_id.in_(scope), model.status.in_(statuses))
            .scalar()
            or 0
        )

    open_risks = status_count(Risk, ["Open", "In Progress"])
    critical_risks = (
        db.query(func.count(Risk.id))
        .filter(
            Risk.project_id.in_(scope),
            Risk.severity == "Critical",
            Risk.status.in_(["Open", "In Progress"]),
        )
        .scalar()
        or 0
    )
    open_blockers = status_count(Blocker, ["Open", "In Progress"])

    summary = {
        "total_projects": len(projects),
        "active_projects": total_active,
        "documents": count(ProjectDocument),
        "open_risks": open_risks,
        "critical_risks": critical_risks,
        "open_blockers": open_blockers,
    }

    # Project health overview (from real metrics only)
    rows = []
    for p in projects:
        health_row = (
            db.query(ProjectInsight)
            .filter(ProjectInsight.project_id == p.id, ProjectInsight.agent == "health")
            .order_by(ProjectInsight.id.desc())
            .first()
        )
        metrics = health_row.payload if health_row else None
        project_open_risks = (
            db.query(Risk)
            .filter(Risk.project_id == p.id, Risk.status.in_(["Open", "In Progress"]))
            .count()
        )
        rows.append(
            {
                "project": project_out(db, p),
                "health": metrics,
                "has_analysis": bool(health_row),
                "risk_level": _risk_level_from_metrics(metrics, project_open_risks),
                "open_risks": project_open_risks,
            }
        )

    my_tasks = (
        db.query(Task)
        .filter(Task.assigned_to == user.id)
        .order_by(Task.id.desc())
        .limit(20)
        .all()
    )

    recent_insights = (
        db.query(ProjectInsight)
        .filter(ProjectInsight.project_id.in_(scope))
        .order_by(ProjectInsight.id.desc())
        .limit(10)
        .all()
    )

    recent_activity = db.query(AuditLog).order_by(AuditLog.id.desc()).limit(12).all()

    recent_docs = (
        db.query(ProjectDocument)
        .filter(ProjectDocument.project_id.in_(scope))
        .order_by(ProjectDocument.uploaded_at.desc())
        .limit(8)
        .all()
    )

    return {
        "summary": summary,
        "projects": rows,
        "my_tasks": [
            {
                "id": t.id,
                "title": t.title,
                "status": t.status,
                "priority": t.priority,
                "due_date": t.due_date,
                "project_id": t.project_id,
            }
            for t in my_tasks
        ],
        "pending_action_items": sum(1 for t in my_tasks if t.status in ("Pending", "In Progress")),
        "completed_tasks": sum(1 for t in my_tasks if t.status == "Completed"),
        "open_blockers": open_blockers,
        "open_risks": open_risks,
        "recent_documents": [
            {
                "id": d.id,
                "original_name": d.original_name,
                "project_id": d.project_id,
                "status": d.status,
                "uploaded_at": d.uploaded_at,
            }
            for d in recent_docs
        ],
        "recent_insights": [
            {
                "id": i.id,
                "agent": i.agent,
                "category": i.category,
                "title": i.title,
                "summary": i.summary,
                "project_id": i.project_id,
                "created_at": i.created_at,
            }
            for i in recent_insights
        ],
        "recent_activity": [
            {
                "id": a.id,
                "action": a.action,
                "user_email": a.user_email,
                "resource_type": a.resource_type,
                "resource_id": a.resource_id,
                "project_id": a.project_id,
                "detail": a.detail,
                "created_at": a.created_at,
            }
            for a in recent_activity
        ],
    }
