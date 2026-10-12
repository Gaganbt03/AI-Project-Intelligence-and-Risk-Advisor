from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_session_workspace
from app.models import AuditLog
from app.models_sessions import WorkspaceSession

router = APIRouter(prefix="/admin/audit-logs", tags=["audit"])


@router.get("")
def list_audit_logs(
    limit: int = Query(default=200, le=1000),
    offset: int = Query(default=0, ge=0),
    action: str | None = None,
    user_id: int | None = None,
    session: WorkspaceSession = Depends(get_session_workspace),
    db: Session = Depends(get_db),
):
    """Audit trail limited to the caller's own workspace.

    The route keeps its legacy path for wire compatibility, but the query is
    always restricted to the session's project so no workspace can read another
    workspace's activity.
    """
    q = db.query(AuditLog).filter(AuditLog.project_id == session.project_id)
    if action:
        q = q.filter(AuditLog.action == action)
    if user_id:
        q = q.filter(AuditLog.user_id == user_id)
    total = q.count()
    rows = q.order_by(AuditLog.id.desc()).offset(offset).limit(limit).all()
    return {
        "total": total,
        "items": [
            {
                "id": r.id,
                "user_id": r.user_id,
                "user_email": r.user_email,
                "action": r.action,
                "resource_type": r.resource_type,
                "resource_id": r.resource_id,
                "project_id": r.project_id,
                "detail": r.detail,
                "ip_address": r.ip_address,
                "created_at": r.created_at,
            }
            for r in rows
        ],
    }
