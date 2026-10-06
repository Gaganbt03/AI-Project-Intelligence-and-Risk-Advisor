from sqlalchemy.orm import Session

from app.models import Role, User
from app.services.security import hash_password

# ``users.role_id`` is a NOT NULL foreign key into ``roles``. The Admin/Employee
# role system has been removed, but the column and table are deliberately kept
# so that no schema migration or table rebuild is required (which would put 26
# existing users and every project-data FK at risk). New accounts are given this
# legacy role id purely to satisfy that constraint; it is never read for
# authorization anywhere in the application.
LEGACY_DEFAULT_ROLE_CODE = "EMPLOYEE"


def ensure_roles(db: Session) -> None:
    """Ensure the legacy role rows exist so ``users.role_id`` stays satisfiable.

    Retained for backward compatibility only. Roles carry no permissions and
    are not used for authorization after the role system removal.
    """
    defaults = [
        ("ADMIN", "Administrator", "Legacy role row retained for schema compatibility"),
        ("EMPLOYEE", "Employee", "Legacy role row retained for schema compatibility"),
    ]
    for code, name, desc in defaults:
        existing = db.query(Role).filter(Role.code == code).first()
        if not existing:
            db.add(Role(code=code, name=name, description=desc))
    db.commit()


def default_role_id(db: Session) -> int:
    """Return the legacy role id assigned to newly created accounts.

    This is a schema-compatibility value only; it grants no privileges.
    """
    role = db.query(Role).filter(Role.code == LEGACY_DEFAULT_ROLE_CODE).first()
    if not role:
        ensure_roles(db)
        role = db.query(Role).filter(Role.code == LEGACY_DEFAULT_ROLE_CODE).first()
    if not role:
        role = db.query(Role).order_by(Role.id).first()
    if not role:
        raise RuntimeError("No role row available to satisfy users.role_id.")
    return role.id


def bootstrap_first_user(db: Session) -> User | None:
    """Create the FIRST user from environment variables, if configured.

    Runs only when zero users exist, so an already-provisioned account is never
    touched. This keeps a headless/server bootstrap path working now that there
    is no separate "administrator" concept. Returns the created user or None.
    """
    from app.config import get_settings

    settings = get_settings()
    if db.query(User).count() > 0:
        return None

    email = (settings.ADMIN_EMAIL or "").strip().lower()
    password = settings.ADMIN_PASSWORD or ""
    if not email or not password or len(password) < 8:
        return None

    user = User(
        email=email,
        name=(settings.ADMIN_NAME or "").strip() or "User",
        password_hash=hash_password(password),
        role_id=default_role_id(db),
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user
