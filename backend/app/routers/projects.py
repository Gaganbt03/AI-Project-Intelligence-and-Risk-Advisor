from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_accessible_project, get_current_user, get_session_workspace
from app.models import Project, User
from app.models_sessions import WorkspaceSession
from app.schemas import ApiResponse, ProjectCreate, ProjectOut, ProjectUpdate
from app.services.audit import log_event
from app.services.projects import (
    ProjectServiceError,
    project_out,
    set_members,
    validate_user_ids,
)

router = APIRouter(prefix="/projects", tags=["projects"])


@router.get("", response_model=list[ProjectOut])
def list_projects(session: WorkspaceSession = Depends(get_session_workspace), db: Session = Depends(get_db)):
    """Return only the caller's own workspace project.

    There is exactly one project per anonymous session, so a workspace never
    sees another workspace's project.
    """
    project = db.query(Project).filter(Project.id == session.project_id).first()
    return [project_out(db, project)] if project else []


@router.post("", response_model=ProjectOut, status_code=201)
def create_project(payload: ProjectCreate, request: Request, actor: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Project creation is not available.

    A workspace is created implicitly with its session; users upload documents
    directly instead of creating projects.
    """
    raise HTTPException(status_code=403, detail="Project creation is not available in a temporary workspace.")


@router.get("/{project_id}", response_model=ProjectOut)
def get_project(project_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    project = get_accessible_project(project_id, user, db)
    return project_out(db, project)


@router.put("/{project_id}", response_model=ProjectOut)
def update_project(project_id: int, payload: ProjectUpdate, request: Request,
                   actor: User = Depends(get_current_user), db: Session = Depends(get_db)):
    project = get_accessible_project(project_id, actor, db)
    try:
        manager_ids = validate_user_ids(db, [payload.manager_id] if payload.manager_id is not None else [])
        member_ids = validate_user_ids(db, payload.member_ids) if payload.member_ids is not None else None
    except ProjectServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if manager_ids and member_ids is not None and manager_ids[0] not in member_ids:
        member_ids.append(manager_ids[0])
    for field in ("name", "description", "objective", "manager_id", "start_date", "expected_end_date", "priority", "status"):
        if field in payload.model_fields_set:
            setattr(project, field, getattr(payload, field))
    db.commit()
    if member_ids is not None:
        set_members(db, project, member_ids, assigned_by=actor.id)
    elif manager_ids and manager_ids[0] not in {m.user_id for m in project.members}:
        set_members(db, project, [m.user_id for m in project.members] + manager_ids, assigned_by=actor.id)
    db.refresh(project)
    log_event(db, user_id=actor.id, user_email=actor.email, action="project_updated",
              resource_type="project", resource_id=str(project.id), project_id=project.id,
              detail=project.name, request=request)
    return project_out(db, project)


@router.delete("/{project_id}", response_model=ApiResponse)
def archive_project(project_id: int, request: Request, actor: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Archive (soft) a project — deletes are avoided to protect audit history."""
    from app.models import utcnow

    project = get_accessible_project(project_id, actor, db)
    project.status = "Archived"
    project.archived_at = utcnow()
    db.commit()
    log_event(db, user_id=actor.id, user_email=actor.email, action="project_archived",
              resource_type="project", resource_id=str(project.id), project_id=project.id,
              detail=project.name, request=request)
    return ApiResponse(ok=True, message="Project archived.")