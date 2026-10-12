from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models import Project, ProjectDocument, User
from app.models_sessions import WorkspaceSession
from app.services.security import decode_token
from app.services import sessions as session_service


def _resolve_session(request: Request, db: Session) -> WorkspaceSession | None:
    """Best-effort session lookup from the HttpOnly cookie (never raises)."""
    settings = get_settings()
    token = request.cookies.get(settings.WORKSPACE_COOKIE_NAME)
    session = session_service.resolve_session(db, token) if token else None
    if session is not None:
        session_service.touch_session(db, session)
    return session


def get_session_workspace(
    request: Request, db: Session = Depends(get_db)
) -> WorkspaceSession:
    """Require a live anonymous workspace session, or raise 401.

    Every project/document-scoped route funnels through this (directly or via
    ``get_accessible_*``), which is what makes cross-session access impossible.
    """
    session = _resolve_session(request, db)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="No active workspace session. Start a session first.",
        )
    return session


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    """Return the actor for a request and stamp the workspace it may touch.

    The login flow is gone, so the actor is a shared placeholder user that only
    exists to satisfy NOT NULL user foreign keys. Authorization is derived from
    the workspace session, not from this actor: the resolved
    ``workspace_project_id`` attribute is the *only* project the request is
    allowed to read or write. A valid Bearer token is still honoured for any
    legacy client, but it never widens project access.
    """
    actor: User | None = None
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        payload = decode_token(auth.split(" ", 1)[1].strip())
        if payload and "sub" in payload:
            candidate = db.query(User).filter(User.id == int(payload["sub"])).first()
            if candidate and candidate.is_active:
                actor = candidate
    if actor is None:
        actor = session_service.ensure_workspace_actor(db)

    session = _resolve_session(request, db)
    # Non-mapped attributes; they carry the request's authorization scope only.
    actor.workspace_project_id = session.project_id if session else None  # type: ignore[attr-defined]
    actor.workspace_session_id = session.id if session else None  # type: ignore[attr-defined]
    return actor


def require_admin(user: User = Depends(get_current_user)) -> User:
    """Deprecated role gate kept only as a no-op passthrough."""
    return user


def _scoped_project_id(user: User | None) -> int:
    project_id = getattr(user, "workspace_project_id", None)
    if project_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="No active workspace session. Start a session first.",
        )
    return project_id


def get_accessible_project(
    project_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Project:
    """Resolve a project that belongs to the caller's workspace.

    A project outside the request's session is reported as 404 (never 403) so
    the existence of another workspace's project is not disclosed.
    """
    scoped = _scoped_project_id(user)
    if project_id != scoped:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found.")
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found.")
    return project


def get_accessible_document(
    document_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ProjectDocument:
    """Resolve a document that belongs to the caller's workspace."""
    scoped = _scoped_project_id(user)
    doc = db.query(ProjectDocument).filter(ProjectDocument.id == document_id).first()
    if not doc or doc.project_id != scoped:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found.")
    return doc
