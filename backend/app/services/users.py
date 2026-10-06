from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import ProjectMember, User
from app.schemas import UserCreate
from app.services.audit import log_event
from app.services.bootstrap import default_role_id
from app.services.projects import ProjectServiceError, user_out, validate_project_ids
from app.services.security import hash_password


class UserServiceError(Exception):
    pass


def create_user(db: Session, data: UserCreate, *, created_by: int | None, request=None) -> User:
    try:
        project_ids = validate_project_ids(db, data.project_ids or [])
    except ProjectServiceError as exc:
        raise UserServiceError(str(exc)) from exc
    if db.query(User).filter(User.email == data.email.lower().strip()).first():
        raise UserServiceError("A user with this email already exists.")

    user = User(
        email=data.email.lower().strip(),
        name=data.name.strip(),
        password_hash=hash_password(data.password),
        role_id=default_role_id(db),
        is_active=True,
        created_by=created_by,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    if project_ids:
        for pid in project_ids:
            db.add(ProjectMember(project_id=pid, user_id=user.id, assigned_by=created_by))
        db.commit()

    log_event(
        db,
        user_id=created_by,
        user_email="",
        action="user_created",
        resource_type="user",
        resource_id=str(user.id),
        detail=user.email,
        request=request,
    )
    return user


def update_user(db: Session, user_id: int, payload, *, actor: User, request=None) -> User:
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise UserServiceError("User not found.")
    # A user cannot deactivate their own account.
    if payload.is_active is not None and user.id == actor.id and not payload.is_active:
        raise UserServiceError("You cannot deactivate your own account.")
    if payload.name is not None:
        user.name = payload.name.strip()
    if payload.password:
        user.password_hash = hash_password(payload.password)
    if payload.is_active is not None:
        user.is_active = payload.is_active
    db.commit()
    db.refresh(user)

    log_event(
        db,
        user_id=actor.id,
        user_email=actor.email,
        action="user_updated",
        resource_type="user",
        resource_id=str(user.id),
        detail=user.email,
        request=request,
    )
    return user


def assign_projects(db: Session, user_id: int, project_ids: list[int], *, actor: User, request=None) -> User:
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise UserServiceError("User not found.")

    try:
        project_ids = validate_project_ids(db, project_ids or [])
    except ProjectServiceError as exc:
        raise UserServiceError(str(exc)) from exc
    db.query(ProjectMember).filter(ProjectMember.user_id == user.id).delete()
    for pid in project_ids:
        db.add(ProjectMember(project_id=pid, user_id=user.id, assigned_by=actor.id))
    db.commit()
    db.refresh(user)

    log_event(
        db,
        user_id=actor.id,
        user_email=actor.email,
        action="user_membership_updated",
        resource_type="user",
        resource_id=str(user.id),
        detail=f"projects={project_ids}",
        request=request,
    )
    return user