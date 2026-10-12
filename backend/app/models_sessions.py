"""Anonymous temporary workspace session model.

One additive table that backs the login-free temporary workspace feature. Each
row binds a random, unguessable token (stored only as a SHA-256 hash) to a
private ``Project`` that owns every document and every piece of derived data for
that browser. Nothing here modifies an existing table, column or row; the table
is created with ``CREATE TABLE IF NOT EXISTS`` semantics via ``init_db``.
"""

from __future__ import annotations

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.orm import relationship

from app.database import Base
from app.models import utcnow


class WorkspaceSession(Base):
    """A single anonymous browser workspace.

    ``project_id`` is unique: one session owns exactly one project, which is the
    isolation boundary for all project-scoped data. ``token_hash`` is unique and
    is the only place the secret is represented - the raw token exists solely in
    the caller's HttpOnly cookie.
    """

    __tablename__ = "workspace_sessions"
    __table_args__ = (
        Index("ix_workspace_session_project", "project_id", unique=True),
    )

    id = Column(Integer, primary_key=True)
    token_hash = Column(String(64), nullable=False, unique=True, index=True)
    project_id = Column(
        Integer,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    owner_label = Column(String(255), nullable=False, default="")
    status = Column(String(20), nullable=False, default="active")  # active|finished|expired
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    last_seen_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    finished_at = Column(DateTime(timezone=True), nullable=True)

    project = relationship("Project", foreign_keys=[project_id])


__all__ = ["WorkspaceSession"]
