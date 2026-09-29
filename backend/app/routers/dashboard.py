from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user, require_admin
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


def _summary_numbers(db: Session, project_ids: list[int] | None = None) -> dict:
    def cnt(model, project_col="project_id"):
        q = db.query(func.count(model.id))
        if project_ids is not None:
            q = q.filter(getattr(model, project_col).in_(project_ids or [-1]))
        return q.scalar() or 0

    if project_ids is None:
        projects = db.query(Project).count()
        active_projects = db.query(Project).filter(Project.status == "Active").count()
        from app.models import ProjectMember

        team = db.query(func.count(func.distinct(ProjectMember.user_id))).scalar() or 0
    else:
        projects = len(project_ids)
        active_projects = (
            db.query(Project)
            .filter(Project.status == "Active", Project.id.in_(project_ids or [-1]))
            .count()
        )
        from app.models import ProjectMember

        team = (
            db.query(func.count(func.distinct(ProjectMember.user_id)))
            .filter(ProjectMember.project_id.in_(project_ids or [-1]))
            .scalar()
            or 0
        )

    def status_cnt(model, statuses):
        q = db.query(func.count(model.id)).filter(model.status.in_(statuses))
        if project_ids is not None:
            q = q.filter(model.project_id.in_(project_ids or [-1]))
        return q.scalar() or 0

    open_risks = status_cnt(Risk, ["Open", "In Progress"])
    critical_risks = (
        db.query(func.count(Risk.id))
        .filter(Risk.severity == "Critical", Risk.status.in_(["Open", "In Progress"]))
    )
    if project_ids is not None:
        critical_risks = critical_risks.filter(Risk.project_id.in_(project_ids or [-1]))
    critical_risks = critical_risks.scalar() or 0
    open_blockers = status_cnt(Blocker, ["Open", "In Progress"])

    return {
        "total_projects": projects,
        "active_projects": active_projects,
        "team_members": team,
        "documents": cnt(ProjectDocument),
        "open_risks": open_risks,
        "critical_risks": critical_risks,
        "open_blockers": open_blockers,
    }


@router.get("/admin/dashboard")
def admin_dashboard(user: User = Depends(require_admin), db: Session = Depends(get_db)):
    numbers = _summary_numbers(db)

    # Project health overview (from real metrics only)
    projects = db.query(Project).order_by(Project.created_at.desc()).all()
    rows = []
    for p in projects:
        health_row = (
            db.query(ProjectInsight)
            .filter(ProjectInsight.project_id == p.id, ProjectInsight.agent == "health")
            .order_by(ProjectInsight.id.desc())
            .first()
        )
        metrics = health_row.payload if health_row else None
        open_risks = (
            db.query(Risk).filter(Risk.project_id == p.id, Risk.status.in_(["Open", "In Progress"])).count()
        )
        rows.append(
            {
                "project": project_out(db, p),
                "health": metrics,
                "has_analysis": bool(health_row),
                "risk_level": _risk_level_from_metrics(metrics, open_risks),
                "open_risks": open_risks,
            }
        )

    recent_insights = (
        db.query(ProjectInsight)
        .order_by(ProjectInsight.id.desc())
        .limit(10)
        .all()
    )
    recent_activity = (
        db.query(AuditLog).order_by(AuditLog.id.desc()).limit(12).all()
    )

    return {
        "summary": numbers,
        "projects": rows,
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


@router.get("/me/dashboard")
def employee_dashboard(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    project_ids = [m.project_id for m in user.memberships]
    if user.role_code == "ADMIN":
        project_ids = [p.id for p in db.query(Project).all()]

    my_projects = []
    if project_ids:
        my_projects = db.query(Project).filter(Project.id.in_(project_ids)).order_by(Project.created_at.desc()).all()

    my_tasks = (
        db.query(Task)
        .filter(Task.assigned_to == user.id)
        .order_by(Task.id.desc())
        .limit(20)
        .all()
    ) if user.role_code != "ADMIN" else []

    open_blockers = (
        db.query(Blocker)
        .filter(Blocker.project_id.in_(project_ids or [-1]), Blocker.status.in_(["Open", "In Progress"]))
        .count()
    )
    open_risks = (
        db.query(Risk)
        .filter(Risk.project_id.in_(project_ids or [-1]), Risk.status.in_(["Open", "In Progress"]))
        .count()
    )
    recent_docs = (
        db.query(ProjectDocument)
        .filter(ProjectDocument.project_id.in_(project_ids or [-1]))
        .order_by(ProjectDocument.uploaded_at.desc())
        .limit(8)
        .all()
    ) if project_ids else []

    recent_insights = (
        db.query(ProjectInsight)
        .filter(ProjectInsight.project_id.in_(project_ids or [-1]))
        .order_by(ProjectInsight.id.desc())
        .limit(10)
        .all()
    )

    pending_tasks = sum(1 for t in my_tasks if t.status in ("Pending", "In Progress"))

    return {
        "projects": [project_out(db, p) for p in my_projects],
        "my_tasks": [
            {"id": t.id, "title": t.title, "status": t.status, "priority": t.priority,
             "due_date": t.due_date, "project_id": t.project_id}
            for t in my_tasks
        ],
        "pending_action_items": pending_tasks,
        "completed_tasks": sum(1 for t in my_tasks if t.status == "Completed"),
        "open_blockers": open_blockers,
        "open_risks": open_risks,
        "recent_documents": [
            {"id": d.id, "original_name": d.original_name, "project_id": d.project_id,
             "status": d.status, "uploaded_at": d.uploaded_at}
            for d in recent_docs
        ],
        "recent_insights": [
            {"id": i.id, "agent": i.agent, "category": i.category, "title": i.title,
             "project_id": i.project_id, "created_at": i.created_at}
            for i in recent_insights
        ],
    }