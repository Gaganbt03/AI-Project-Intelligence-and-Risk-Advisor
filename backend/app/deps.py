from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Project, ProjectDocument, User
from app.services.security import decode_token

_bearer_scheme_msg = "Authorization required."


def _get_token(request: Request) -> str:
    auth = request.headers.get("Authorization", "")
    if not auth.lower().startswith("bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=_bearer_scheme_msg)
    return auth.split(" ", 1)[1].strip()


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    token = _get_token(request)
    payload = decode_token(token)
    if not payload or "sub" not in payload:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token.")
    user = db.query(User).filter(User.id == int(payload["sub"])).first()
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Account is inactive or missing.")
    return user


def require_admin(user: User = Depends(get_current_user)) -> User:
    """Deprecated role gate kept only as a no-op passthrough.

    The Admin/Employee role system has been removed. Every dependency that
    referenced this function is being migrated to ``get_current_user`` so that
    authentication (not role membership) is the single access requirement.
    """
    return user


def get_accessible_project(project_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> Project:
    """Resolve a project for an authenticated user.

    Access is no longer role- or membership-gated: every logged-in user works
    with every project in this single-tier application. The lookup is kept so
    that unknown project ids still return a clean 404 instead of leaking
    downstream errors, and so project-scoped Milestone 1/2/3 routes keep their
    existing shape.
    """
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found.")
    return project


def get_accessible_document(document_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> ProjectDocument:
    """Resolve a document for an authenticated user (no role/membership gate)."""
    doc = db.query(ProjectDocument).filter(ProjectDocument.id == document_id).first()
    if not doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found.")
    return doc