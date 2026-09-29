from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Project, ProjectDocument, ProjectMember, User
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
    if user.role_code != "ADMIN":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Administrator access required.")
    return user


def get_accessible_project(project_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> Project:
    """Admins can access any non-archived-or-archived project; employees only their assigned projects."""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found.")
    if user.role_code != "ADMIN":
        membership = (
            db.query(ProjectMember)
            .filter(ProjectMember.project_id == project_id, ProjectMember.user_id == user.id)
            .first()
        )
        if not membership:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You do not have access to this project.")
    return project


def get_member_or_leave(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> tuple[User, Session]:
    return user, db


def get_accessible_document(document_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> ProjectDocument:
    doc = db.query(ProjectDocument).filter(ProjectDocument.id == document_id).first()
    if not doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found.")
    if user.role_code != "ADMIN":
        membership = (
            db.query(ProjectMember)
            .filter(ProjectMember.project_id == doc.project_id, ProjectMember.user_id == user.id)
            .first()
        )
        if not membership:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have access to this document's project.",
            )
    return doc