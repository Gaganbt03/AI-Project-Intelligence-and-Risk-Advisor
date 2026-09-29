from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import Project, ProjectMember, User
from app.schemas import ProjectCreate, ProjectOut, ProjectUpdate, UserOut


class ProjectServiceError(Exception):
    pass


def validate_project_ids(db: Session, project_ids: list[int]) -> list[int]:
    ids = set(project_ids or [])
    if not ids:
        return []
    found = db.query(Project.id).filter(Project.id.in_(ids)).all()
    found_ids = {row[0] for row in found}
    missing = sorted(ids - found_ids)
    if missing:
        raise ProjectServiceError(f"Unknown project IDs: {missing}.")
    return list(ids)


def validate_user_ids(db: Session, user_ids: list[int]) -> list[int]:
    ids = set(user_ids or [])
    if not ids:
        return []
    found = db.query(User.id).filter(User.id.in_(ids)).all()
    found_ids = {row[0] for row in found}
    missing = sorted(ids - found_ids)
    if missing:
        raise ProjectServiceError(f"Unknown user IDs: {missing}.")
    return list(ids)


def project_out(db: Session, project: Project, current_user: User | None = None) -> ProjectOut:
    out = ProjectOut(
        id=project.id,
        name=project.name,
        description=project.description or "",
        objective=project.objective or "",
        manager_id=project.manager_id,
        manager_name=project.manager.name if project.manager else None,
        start_date=project.start_date,
        expected_end_date=project.expected_end_date,
        priority=project.priority,
        status=project.status,
        created_at=project.created_at,
        member_count=len(project.members),
        document_count=len(project.documents) if project.documents else _count_docs(db, project.id),
        task_count=len(project.tasks) if project.tasks else _count_tasks(db, project.id),
        member_ids=[m.user_id for m in project.members],
    )
    return out


def _count_docs(db: Session, project_id: int) -> int:
    from app.models import ProjectDocument

    return db.query(ProjectDocument).filter(ProjectDocument.project_id == project_id).count()


def _count_tasks(db: Session, project_id: int) -> int:
    from app.models import Task

    return db.query(Task).filter(Task.project_id == project_id).count()


def set_members(db: Session, project: Project, member_ids: list[int], assigned_by: int | None = None) -> None:
    existing = {m.user_id: m for m in project.members}
    target = set(member_ids or [])
    for uid in existing:
        if uid not in target:
            db.query(ProjectMember).filter(
                ProjectMember.project_id == project.id, ProjectMember.user_id == uid
            ).delete()
    for uid in target:
        if uid not in existing:
            db.add(
                ProjectMember(project_id=project.id, user_id=uid, assigned_by=assigned_by)
            )
    db.commit()


def user_out(db: Session, user: User) -> UserOut:
    return UserOut(
        id=user.id,
        email=user.email,
        name=user.name,
        role=user.role_code,
        is_active=user.is_active,
        created_at=user.created_at,
        last_login=user.last_login,
        project_ids=[m.project_id for m in user.memberships],
    )


def list_projects_for_user(db: Session, user: User) -> list[Project]:
    if user.role_code == "ADMIN":
        projects = db.query(Project).order_by(Project.created_at.desc()).all()
    else:
        ids = [m.project_id for m in user.memberships]
        projects = []
        if ids:
            projects = db.query(Project).filter(Project.id.in_(ids)).order_by(Project.created_at.desc()).all()
    # Hide archived from default list? No - archiving is a status; still visible but marked.
    return projects