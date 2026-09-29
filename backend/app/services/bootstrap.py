from sqlalchemy.orm import Session

from app.models import Role, User
from app.services.security import hash_password


def ensure_roles(db: Session) -> None:
    """Create the two base roles. Additional roles can be added later."""
    defaults = [
        ("ADMIN", "Administrator", "Full system administration access"),
        ("EMPLOYEE", "Employee", "Project team member access"),
    ]
    for code, name, desc in defaults:
        existing = db.query(Role).filter(Role.code == code).first()
        if not existing:
            db.add(Role(code=code, name=name, description=desc))
    db.commit()


def bootstrap_admin(db: Session) -> User | None:
    """Create the FIRST administrator from environment variables (if set).

    Only runs when zero users exist, so a pre-created admin is never touched.
    Returns the created user or None.
    """
    from app.config import get_settings

    settings = get_settings()
    if db.query(User).count() > 0:
        return None

    email = (settings.ADMIN_EMAIL or "").strip().lower()
    password = settings.ADMIN_PASSWORD or ""
    if not email or not password or len(password) < 8:
        return None

    role = db.query(Role).filter(Role.code == "ADMIN").first()
    if not role:
        return None

    user = User(
        email=email,
        name=settings.ADMIN_NAME.strip() or "Administrator",
        password_hash=hash_password(password),
        role_id=role.id,
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def count_users(db: Session) -> int:
    return db.query(User).count()