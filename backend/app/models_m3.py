"""Milestone 3 ORM models.

Five additive tables that extend the Milestone 1/2 schema. Nothing here
modifies or replaces an existing table, column or row:

* ``generated_documents``     - AI-authored documentation, kept strictly
                                 separate from the user's uploaded
                                 ``documents`` rows.
* ``health_snapshots``        - one deterministic health evaluation per
                                 request/refresh, so scores are auditable.
* ``assistant_conversations`` - a chat thread, always bound to ONE project.
* ``assistant_messages``      - turns inside a conversation.
* ``project_pipeline_runs``   - progress of the automatic post-upload analysis
                                 run. Progress only; never a source of truth.

All tables are created with ``CREATE TABLE IF NOT EXISTS`` semantics (see
``app/database.py``), so applying the schema to an existing database is
non-destructive and every existing row stays valid.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from app.database import Base
from app.models import Project, utcnow


class GeneratedDocument(Base):
    """AI-generated documentation for a project.

    Deliberately NOT stored in ``ProjectDocument``: uploaded originals are
    immutable user data and must never be overwritten by a regeneration.
    One row per (project, doc_type); regenerating replaces the *content* of
    that row in place and keeps the same identity.
    """

    __tablename__ = "generated_documents"
    __table_args__ = (
        UniqueConstraint("project_id", "doc_type", name="uq_generated_document_type"),
        Index("ix_generated_doc_project", "project_id"),
    )

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False, index=True)
    doc_type = Column(String(50), nullable=False)  # user_stories|risk_register|action_items
    title = Column(String(255), nullable=False, default="")
    content = Column(Text, nullable=False, default="")  # rendered Markdown
    payload = Column(JSON, nullable=False, default=dict)  # structured sections
    file_name = Column(String(255), nullable=False, default="")
    file_type = Column(String(20), nullable=False, default="md")
    generation_count = Column(Integer, nullable=False, default=1)
    insufficient_evidence = Column(Boolean, nullable=False, default=False)
    provider = Column(String(100), nullable=False, default="")
    model = Column(String(100), nullable=False, default="")
    error = Column(Text, nullable=True)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    project = relationship("Project", back_populates="generated_documents")
    created_by_user = relationship("User", foreign_keys=[created_by])

    @property
    def section_count(self) -> int:
        return len(self.payload.get("sections", [])) if self.payload else 0


class HealthSnapshot(Base):
    """Immutable record of one deterministic health evaluation."""

    __tablename__ = "health_snapshots"
    __table_args__ = (Index("ix_health_snapshot_project", "project_id"),)

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False, index=True)
    overall_score = Column(Integer, nullable=False, default=0)  # 0-100
    status = Column(String(30), nullable=False, default="Unknown")
    dimensions = Column(JSON, nullable=False, default=dict)     # {key: {score, weight, ...}}
    formula_version = Column(String(20), nullable=False, default="m3.2")
    inputs = Column(JSON, nullable=False, default=dict)        # exact inputs used
    positives = Column(JSON, nullable=False, default=list)
    negatives = Column(JSON, nullable=False, default=list)
    recommendations = Column(JSON, nullable=False, default=list)
    narrative = Column(Text, nullable=False, default="")        # LLM explanation (never the number)
    narrative_provider = Column(String(100), nullable=False, default="")
    narrative_model = Column(String(100), nullable=False, default="")
    data_completeness = Column(Integer, nullable=False, default=0)  # 0-100
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    project = relationship("Project", back_populates="health_snapshots")


class AssistantConversation(Base):
    """A chat thread. Always scoped to a single project - there is no path in
    the application that reads messages across projects."""

    __tablename__ = "assistant_conversations"
    __table_args__ = (Index("ix_conversation_project", "project_id"),)

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False, index=True)
    title = Column(String(255), nullable=False, default="")
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    project = relationship("Project", back_populates="assistant_conversations")
    messages = relationship(
        "AssistantMessage",
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="AssistantMessage.id",
    )
    created_by_user = relationship("User", foreign_keys=[created_by])


class AssistantMessage(Base):
    """One turn in a conversation. `evidence` stores the source refs that
    grounded the answer so any past turn remains auditable."""

    __tablename__ = "assistant_messages"
    __table_args__ = (Index("ix_message_conversation", "conversation_id"),)

    id = Column(Integer, primary_key=True)
    conversation_id = Column(
        Integer, ForeignKey("assistant_conversations.id"), nullable=False, index=True
    )
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False, index=True)
    role = Column(String(20), nullable=False)  # user | assistant
    content = Column(Text, nullable=False, default="")
    evidence = Column(JSON, nullable=False, default=list)
    sources = Column(JSON, nullable=False, default=list)
    retrieval_score = Column(Integer, nullable=False, default=0)   # 0-100 relevance
    grounded = Column(Boolean, nullable=False, default=False)
    used_structured_data = Column(Boolean, nullable=False, default=False)
    used_conversation_context = Column(Boolean, nullable=False, default=False)
    insufficient_evidence = Column(Boolean, nullable=False, default=False)
    provider = Column(String(100), nullable=False, default="")
    model = Column(String(100), nullable=False, default="")
    error = Column(Text, nullable=True)
    validation = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    conversation = relationship("AssistantConversation", back_populates="messages")


class ProjectPipelineRun(Base):
    """One automatic post-upload analysis run, stage by stage.

    Additive table: it records *progress* only. Nothing here is a source of
    truth — every stage's real result still lives in the existing
    ``project_insights`` / ``risks`` / ``blockers`` / ``tasks`` /
    ``health_snapshots`` / ``generated_documents`` tables, so deleting this
    table would lose progress history and nothing else.

    ``stages`` is a JSON list of ``{key, label, status, detail}`` where status is
    one of ``pending`` / ``running`` / ``done`` / ``skipped`` / ``failed``.
    """

    __tablename__ = "project_pipeline_runs"

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    document_id = Column(Integer, ForeignKey("documents.id", ondelete="CASCADE"), nullable=True)
    trigger = Column(String(40), nullable=False, default="document_upload")
    status = Column(String(20), nullable=False, default="Running")
    current_stage = Column(String(60), nullable=False, default="")
    stages = Column(JSON, nullable=False, default=list)
    counts = Column(JSON, nullable=False, default=dict)
    due_date = Column(JSON, nullable=False, default=dict)
    error = Column(Text, nullable=True)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    finished_at = Column(DateTime(timezone=True), nullable=True)

    project = relationship("Project", foreign_keys=[project_id])

    __table_args__ = (
        Index("ix_pipeline_runs_project_created", "project_id", "created_at"),
    )


# Register the reverse relationships on the Milestone 1 Project model without
# touching its definition, keeping this change purely additive.
Project.generated_documents = relationship(  # type: ignore[attr-defined]
    "GeneratedDocument", back_populates="project", cascade="all, delete-orphan"
)
Project.health_snapshots = relationship(  # type: ignore[attr-defined]
    "HealthSnapshot", back_populates="project", cascade="all, delete-orphan"
)
Project.assistant_conversations = relationship(  # type: ignore[attr-defined]
    "AssistantConversation", back_populates="project", cascade="all, delete-orphan"
)

__all__ = [
    "GeneratedDocument",
    "HealthSnapshot",
    "AssistantConversation",
    "AssistantMessage",
    "ProjectPipelineRun",
]
