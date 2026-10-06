from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user
from app.models import User
from app.schemas import UserCreate, UserOut, UserProjectsUpdate, UserUpdate, ApiResponse
from app.services.projects import user_out
from app.services.users import UserServiceError, assign_projects, create_user, update_user

router = APIRouter(prefix="/users", tags=["users"])


def _users(db: Session) -> list[UserOut]:
    users = db.query(User).order_by(User.created_at.desc()).all()
    return [user_out(db, u) for u in users]


@router.get("", response_model=list[UserOut])
def list_users(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """List accounts.

    Also the source for project manager / task assignee pickers, so it stays
    available to every authenticated user rather than being admin-only.
    """
    return _users(db)


@router.post("", response_model=UserOut, status_code=201)
def create_account(payload: UserCreate, request: Request, actor: User = Depends(get_current_user), db: Session = Depends(get_db)):
    try:
        user = create_user(db, payload, created_by=actor.id, request=request)
    except UserServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return user_out(db, user)


@router.put("/{user_id}", response_model=UserOut)
def edit_account(user_id: int, payload: UserUpdate, request: Request, actor: User = Depends(get_current_user), db: Session = Depends(get_db)):
    try:
        user = update_user(db, user_id, payload, actor=actor, request=request)
    except UserServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return user_out(db, user)


@router.put("/{user_id}/projects", response_model=UserOut)
def set_user_projects(user_id: int, payload: UserProjectsUpdate, request: Request, actor: User = Depends(get_current_user), db: Session = Depends(get_db)):
    try:
        user = assign_projects(db, user_id, payload.project_ids, actor=actor, request=request)
    except UserServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return user_out(db, user)


@router.post("/{user_id}/reset-password", response_model=ApiResponse)
def reset_password(user_id: int, payload: UserUpdate, request: Request, actor: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Password reset (requires a new password to be supplied)."""
    if not payload.password:
        raise HTTPException(status_code=400, detail="A new password is required.")
    try:
        update_user(db, user_id, UserUpdate(password=payload.password), actor=actor, request=request)
    except UserServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return ApiResponse(ok=True, message="Password reset.")
