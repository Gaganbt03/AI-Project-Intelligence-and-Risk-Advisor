"""Anonymous temporary workspace session service.

Provides the primitives behind the login-free workspace:

* ``ensure_workspace_actor`` - a persisted user row used only to satisfy the
  NOT NULL user foreign keys (``uploaded_by`` / ``created_by``). It grants no
  privileges; isolation is enforced by session ownership, never by this actor.
* ``create_session`` - create a fresh private project and bind a new token.
* ``resolve_session`` - look up a live session from a raw cookie token.
* ``touch_session`` - sliding idle expiry, written at most once per interval.

The heavy lifting of deleting a session (finish / expiry) lives in
``purge_workspace_session`` so both the explicit "Finish & Clear Session" route
and the background sweeper share one, FK-safe implementation.
"""

from __future__ import annotations

import hashlib
import secrets
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import (
    AiRun,
    AuditLog,
    Blocker,
    DocumentChunk,
    Project,
    ProjectDocument,
    ProjectInsight,
    ProjectMember,
    Risk,
    Task,
    User,
)
from app.models_m3 import (
    AssistantConversation,
    AssistantMessage,
    GeneratedDocument,
    HealthSnapshot,
    ProjectPipelineRun,
)
from app.models_sessions import WorkspaceSession
from app.services.bootstrap import default_role_id
from app.services.security import hash_password
from app.services.vector_store import get_vector_store

ACTIVE = "active"
CLOSING = "closing"  # browser-close signalled; purge after a short grace
FINISHED = "finished"
EXPIRED = "expired"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_aware(value: datetime | None) -> datetime | None:
    """Normalise datetimes read back from SQLite (which drops tzinfo)."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def ensure_workspace_actor(db: Session) -> User:
    """Return a persisted user used purely to satisfy NOT NULL user FKs.

    Prefers an existing active account; creates a single legacy-compatible
    account only when the database has no user at all (e.g. a fresh install).
    """
    user = (
        db.query(User)
        .filter(User.is_active == True)  # noqa: E712
        .order_by(User.id.asc())
        .first()
    )
    if user:
        return user
    user = db.query(User).order_by(User.id.asc()).first()
    if user:
        return user
    user = User(
        email="workspace@local",
        name="Workspace",
        password_hash=hash_password(secrets.token_urlsafe(24)),
        role_id=default_role_id(db),
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def create_session(db: Session, *, owner_label: str = "") -> tuple[WorkspaceSession, str]:
    """Create a fresh isolated workspace. Returns ``(session, raw_token)``.

    The raw token is returned exactly once; only its hash is persisted.
    """
    settings = get_settings()
    actor = ensure_workspace_actor(db)
    project = Project(
        name="Workspace",
        description="Temporary anonymous workspace",
        objective="",
        status="Active",
        priority="Medium",
        created_by=actor.id,
    )
    db.add(project)
    db.commit()
    db.refresh(project)

    token = secrets.token_urlsafe(32)
    now = _now()
    session = WorkspaceSession(
        token_hash=hash_token(token),
        project_id=project.id,
        owner_label=(owner_label or "")[:255],
        status=ACTIVE,
        created_at=now,
        last_seen_at=now,
        expires_at=now + timedelta(minutes=settings.WORKSPACE_SESSION_TTL_MINUTES),
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return session, token


def resolve_session(db: Session, token: str | None) -> WorkspaceSession | None:
    """Return the live session for a raw token, or ``None``.

    A ``closing`` session is still resolvable: any returning request cancels the
    pending browser-close purge via :func:`touch_session`.
    """
    if not token:
        return None
    session = (
        db.query(WorkspaceSession)
        .filter(WorkspaceSession.token_hash == hash_token(token))
        .first()
    )
    if not session or session.status not in (ACTIVE, CLOSING):
        return None
    expires = _as_aware(session.expires_at)
    if expires is not None and expires <= _now():
        return None
    return session


def touch_session(db: Session, session: WorkspaceSession, min_interval_seconds: int = 60) -> None:
    """Slide the idle expiry forward, writing at most once per interval.

    Also cancels a pending browser-close: a request arriving during the grace
    window revives the session (quick refresh must not lose work).
    """
    settings = get_settings()
    now = _now()
    if session.status == CLOSING:
        session.status = ACTIVE
        session.finished_at = None
        session.last_seen_at = now
        session.expires_at = now + timedelta(minutes=settings.WORKSPACE_SESSION_TTL_MINUTES)
        db.commit()
        return
    last = _as_aware(session.last_seen_at)
    if last is None or (now - last).total_seconds() >= min_interval_seconds:
        session.last_seen_at = now
        session.expires_at = now + timedelta(minutes=settings.WORKSPACE_SESSION_TTL_MINUTES)
        db.commit()


def session_project(db: Session, session: WorkspaceSession) -> Project | None:
    return db.query(Project).filter(Project.id == session.project_id).first()


def schedule_close(db: Session, session: WorkspaceSession) -> WorkspaceSession:
    """Mark a session ``closing`` so the sweeper purges it after the grace."""
    session.status = CLOSING
    session.finished_at = _now()
    db.commit()
    return session


def _purge_workspace(db: Session, session_id: int, project_id: int) -> dict[str, int]:
    """Hard-delete every row owned by one workspace, then its files/vectors.

    Children are removed before parents so the delete order stays valid under
    ``PRAGMA foreign_keys=ON`` without relying on ORM cascade behaviour.
    """

    def _bulk(model, condition) -> int:
        return int(db.query(model).filter(condition).delete(synchronize_session=False))

    counts: dict[str, int] = {
        "assistant_messages": _bulk(AssistantMessage, AssistantMessage.project_id == project_id),
        "assistant_conversations": _bulk(
            AssistantConversation, AssistantConversation.project_id == project_id
        ),
        "project_insights": _bulk(ProjectInsight, ProjectInsight.project_id == project_id),
        "ai_runs": _bulk(AiRun, AiRun.project_id == project_id),
        "document_chunks": _bulk(DocumentChunk, DocumentChunk.project_id == project_id),
        "project_pipeline_runs": _bulk(
            ProjectPipelineRun, ProjectPipelineRun.project_id == project_id
        ),
        "risks": _bulk(Risk, Risk.project_id == project_id),
        "blockers": _bulk(Blocker, Blocker.project_id == project_id),
        "tasks": _bulk(Task, Task.project_id == project_id),
        "generated_documents": _bulk(GeneratedDocument, GeneratedDocument.project_id == project_id),
        "health_snapshots": _bulk(HealthSnapshot, HealthSnapshot.project_id == project_id),
        "project_members": _bulk(ProjectMember, ProjectMember.project_id == project_id),
        "audit_logs": _bulk(AuditLog, AuditLog.project_id == project_id),
        "documents": _bulk(ProjectDocument, ProjectDocument.project_id == project_id),
        "workspace_sessions": _bulk(WorkspaceSession, WorkspaceSession.id == session_id),
        "projects": _bulk(Project, Project.id == project_id),
    }
    db.commit()

    settings = get_settings()
    project_dir = Path(settings.UPLOAD_DIR) / str(project_id)
    if project_dir.exists():
        shutil.rmtree(project_dir, ignore_errors=True)

    get_vector_store().reset_project(project_id)

    return counts


def purge_workspace_session(db: Session, session: WorkspaceSession) -> dict[str, int]:
    """Permanently delete all data for the session's workspace."""
    return _purge_workspace(db, session.id, session.project_id)


def sweep_workspaces(db: Session, *, grace_seconds: int | None = None) -> dict[str, object]:
    """Purge ``closing`` sessions past grace and ``active`` sessions past TTL.

    Intended to be called periodically from a background loop (see ``main.py``)
    so abandoned workspaces are reclaimed even if the browser never signals.
    """
    settings = get_settings()
    grace = settings.WORKSPACE_FINISH_GRACE_SECONDS if grace_seconds is None else grace_seconds
    now = _now()

    # Snapshot the fields we need: purging commits, which expires ORM instances.
    candidates = [
        (s.id, s.project_id, s.status, _as_aware(s.finished_at), _as_aware(s.expires_at))
        for s in db.query(WorkspaceSession)
        .filter(WorkspaceSession.status.in_([ACTIVE, CLOSING]))
        .all()
    ]

    purged = {"closed": 0, "expired": 0, "projects": []}
    for session_id, project_id, status, finished_at, expires_at in candidates:
        should_purge = False
        if status == CLOSING:
            if finished_at is not None and (now - finished_at).total_seconds() >= grace:
                should_purge = True
                purged["closed"] = int(purged["closed"]) + 1
        elif expires_at is not None and expires_at <= now:
            should_purge = True
            purged["expired"] = int(purged["expired"]) + 1
        if should_purge:
            _purge_workspace(db, session_id, project_id)
            purged["projects"].append(project_id)  # type: ignore[union-attr]
    return purged
