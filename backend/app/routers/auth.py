from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user
from app.models import Role, User, utcnow
from app.schemas import ApiResponse, ChangePasswordRequest, LoginRequest, LoginResponse, SetupAdminRequest, UserSummary
from app.services.audit import log_event
from app.services.bootstrap import ensure_roles
from app.services.security import create_access_token, hash_password, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])


def _summary(user: User) -> UserSummary:
    return UserSummary(id=user.id, email=user.email, name=user.name, role=user.role_code, is_active=user.is_active)


def _issue_token(db: Session, user: User, request: Request) -> LoginResponse:
    user.last_login = utcnow()
    db.commit()
    log_event(db, user_id=user.id, user_email=user.email, action="login", detail="User login", request=request)
    token = create_access_token(user.id, user.role_code)
    return LoginResponse(access_token=token, user=_summary(user))


@router.get("/status")
def auth_status(db: Session = Depends(get_db)):
    """Indicates whether the first administrator still needs to be created."""
    needs_setup = db.query(User).count() == 0
    return {"needs_setup": needs_setup}


@router.post("/setup-admin", status_code=status.HTTP_201_CREATED)
def setup_admin(payload: SetupAdminRequest, request: Request, db: Session = Depends(get_db)):
    """Secure first-admin creation. Only valid while there are zero users."""
    if db.query(User).count() > 0:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Setup already completed.")
    ensure_roles(db)
    role = db.query(Role).filter(Role.code == "ADMIN").first()
    user = User(
        email=payload.email.lower().strip(),
        name=payload.name.strip(),
        password_hash=hash_password(payload.password),
        role_id=role.id,
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    log_event(db, user_id=user.id, user_email=user.email, action="admin_created", detail="First administrator created", request=request)
    return _issue_token(db, user, request)


@router.post("/login", response_model=LoginResponse)
def login(payload: LoginRequest, request: Request, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == payload.email.lower().strip()).first()
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password.")
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account is deactivated.")
    return _issue_token(db, user, request)


@router.post("/logout", response_model=ApiResponse)
def logout(user: User = Depends(get_current_user), request: Request = None, db: Session = Depends(get_db)):
    log_event(db, user_id=user.id, user_email=user.email, action="logout", detail="User logout", request=request)
    return ApiResponse(ok=True, message="Logged out.")


@router.get("/me", response_model=UserSummary)
def me(user: User = Depends(get_current_user)):
    return _summary(user)


@router.post("/change-password", response_model=ApiResponse)
def change_password(payload: ChangePasswordRequest, user: User = Depends(get_current_user), request: Request = None, db: Session = Depends(get_db)):
    if not verify_password(payload.old_password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Current password is incorrect.")
    user.password_hash = hash_password(payload.new_password)
    db.commit()
    log_event(db, user_id=user.id, user_email=user.email, action="password_changed", detail="Password changed", request=request)
    return ApiResponse(ok=True, message="Password updated.")