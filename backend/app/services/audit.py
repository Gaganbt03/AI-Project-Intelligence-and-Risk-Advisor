from datetime import datetime, timezone

from fastapi import Request
from sqlalchemy.orm import Session

from app.models import AuditLog


def log_event(
    db: Session,
    *,
    user_id: int | None,
    user_email: str,
    action: str,
    resource_type: str = "",
    resource_id: str = "",
    project_id: int | None = None,
    detail: str = "",
    request: Request | None = None,
) -> None:
    ip = ""
    if request and request.client:
        ip = request.client.host
    entry = AuditLog(
        user_id=user_id,
        user_email=user_email or "",
        action=action,
        resource_type=resource_type,
        resource_id=str(resource_id) if resource_id is not None else "",
        project_id=project_id,
        detail=detail[:2000],
        ip_address=ip or "",
        created_at=datetime.now(timezone.utc),
    )
    db.add(entry)
    db.commit()

    _PRUNE_AFTER = 20000
    count = db.query(AuditLog).count()
    if count > _PRUNE_AFTER:
        stale = db.query(AuditLog).order_by(AuditLog.created_at.asc()).limit(count - _PRUNE_AFTER).all()
        for s in stale:
            db.delete(s)
        db.commit()