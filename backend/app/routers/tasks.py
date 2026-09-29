from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_accessible_project, get_current_user
from app.models import ProjectDocument, ProjectMember, Task, User
from app.services.audit import log_event

router = APIRouter(prefix="/tasks", tags=["tasks"])
TASK_STATUSES = {"Pending", "In Progress", "Completed", "Blocked"}
TASK_PRIORITIES = {"Low", "Medium", "High", "Critical"}


def _validate_assignee(db: Session, project_id: int, assignee_id: int | None) -> None:
    if assignee_id is None:
        return
    membership = db.query(ProjectMember).filter(
        ProjectMember.project_id == project_id,
        ProjectMember.user_id == assignee_id,
    ).first()
    if not membership:
        raise HTTPException(status_code=400, detail="Assignee must be a member of this project.")


def _task_out(t: Task) -> dict:
    return {
        "id": t.id,
        "project_id": t.project_id,
        "title": t.title,
        "description": t.description,
        "assigned_to": t.assigned_to,
        "assigned_name": t.assignee.name if t.assignee else "",
        "due_date": t.due_date,
        "priority": t.priority,
        "status": t.status,
        "source_type": t.source_type,
        "source_ref": t.source_ref,
        "source_document_id": t.source_document_id,
        "source_document": t.source_document.original_name if t.source_document else "",
        "ai_generated": t.ai_generated,
        "created_by": t.created_by,
        "created_at": t.created_at,
        "updated_at": t.updated_at,
    }


def _parse_date(value) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


@router.get("")
def list_tasks(
    project_id: int = Query(...),
    status: str | None = Query(default=None),
    assigned_to: int | None = Query(default=None),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    get_accessible_project(project_id, user, db)
    q = db.query(Task).filter(Task.project_id == project_id)
    if status:
        q = q.filter(Task.status == status)
    if assigned_to is not None:
        q = q.filter(Task.assigned_to == assigned_to)
    return [_task_out(t) for t in q.order_by(Task.id.desc()).all()]


@router.post("", status_code=201)
def create_task(
    project_id: int,
    payload: dict,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    get_accessible_project(project_id, user, db)
    if user.role_code != "ADMIN":
        raise HTTPException(status_code=403, detail="Only administrators can create manual tasks.")
    title = (payload.get("title") or "").strip()[:255]
    if not title:
        raise HTTPException(status_code=400, detail="Task title is required.")
    status = payload.get("status") or "Pending"
    priority = payload.get("priority") or "Medium"
    if status not in TASK_STATUSES:
        raise HTTPException(status_code=400, detail="Invalid task status.")
    if priority not in TASK_PRIORITIES:
        raise HTTPException(status_code=400, detail="Invalid task priority.")
    _validate_assignee(db, project_id, payload.get("assigned_to"))
    source_doc_id = payload.get("source_document_id")
    if source_doc_id:
        doc = db.query(ProjectDocument).filter(
            ProjectDocument.id == source_doc_id, ProjectDocument.project_id == project_id
        ).first()
        if not doc:
            raise HTTPException(status_code=400, detail="Source document does not belong to this project.")
    task = Task(
        project_id=project_id,
        title=title,
        description=payload.get("description") or "",
        assigned_to=payload.get("assigned_to"),
        due_date=_parse_date(payload.get("due_date")),
        priority=priority,
        status=status,
        source_type="manual",
        source_document_id=source_doc_id,
        source_ref=payload.get("source_ref") or "",
        ai_generated=False,
        created_by=user.id,
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    log_event(db, user_id=user.id, user_email=user.email, action="task_created",
              resource_type="task", resource_id=str(task.id), project_id=project_id,
              detail=title)
    return _task_out(task)


@router.put("/{task_id}")
def update_task(task_id: int, payload: dict, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found.")
    get_accessible_project(task.project_id, user, db)

    is_admin = user.role_code == "ADMIN"
    is_assignee = task.assigned_to == user.id
    if not is_admin and not is_assignee:
        raise HTTPException(status_code=403, detail="Only the assignee or an administrator can update this task.")
    if not is_admin and any(field != "status" for field in payload):
        raise HTTPException(status_code=400, detail="Employees may only update task status.")

    allowed_fields = (
        {"status", "title", "description", "priority", "due_date", "source_ref", "source_document_id"}
        if is_admin
        else {"status"}
    )
    for field in allowed_fields:
        if field in payload:
            value = payload[field]
            if field == "due_date":
                value = _parse_date(value)
            elif field == "status" and value not in TASK_STATUSES:
                raise HTTPException(status_code=400, detail="Invalid task status.")
            elif field == "priority" and value not in TASK_PRIORITIES:
                raise HTTPException(status_code=400, detail="Invalid task priority.")
            elif field == "source_document_id" and value is not None:
                source = db.query(ProjectDocument).filter(
                    ProjectDocument.id == value,
                    ProjectDocument.project_id == task.project_id,
                ).first()
                if not source:
                    raise HTTPException(status_code=400, detail="Source document does not belong to this project.")
            setattr(task, field, value)
    if is_admin and "assigned_to" in payload:
        _validate_assignee(db, task.project_id, payload["assigned_to"])
        task.assigned_to = payload["assigned_to"]
    db.commit()
    db.refresh(task)
    log_event(db, user_id=user.id, user_email=user.email, action="task_updated",
              resource_type="task", resource_id=str(task.id), project_id=task.project_id,
              detail=task.title)
    return _task_out(task)