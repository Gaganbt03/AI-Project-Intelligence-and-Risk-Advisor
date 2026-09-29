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


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TimestampMixin:
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class Role(Base):
    __tablename__ = "roles"

    id = Column(Integer, primary_key=True)
    code = Column(String(50), unique=True, nullable=False, index=True)
    name = Column(String(100), nullable=False)
    description = Column(Text, default="")
    created_at = Column(DateTime(timezone=True), default=utcnow)

    users = relationship("User", back_populates="role")


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    email = Column(String(255), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    name = Column(String(255), nullable=False)
    role_id = Column(Integer, ForeignKey("roles.id"), nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    last_login = Column(DateTime(timezone=True), nullable=True)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)

    role = relationship("Role", back_populates="users")
    memberships = relationship(
        "ProjectMember",
        foreign_keys="ProjectMember.user_id",
        back_populates="user",
        cascade="all, delete-orphan",
    )
    uploaded_documents = relationship("ProjectDocument", foreign_keys="ProjectDocument.uploaded_by")

    @property
    def role_code(self) -> str:
        return self.role.code if self.role else ""


class Project(TimestampMixin, Base):
    __tablename__ = "projects"

    id = Column(Integer, primary_key=True)
    name = Column(String(255), nullable=False, index=True)
    description = Column(Text, default="")
    objective = Column(Text, default="")
    manager_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    start_date = Column(DateTime(timezone=True), nullable=True)
    expected_end_date = Column(DateTime(timezone=True), nullable=True)
    priority = Column(String(20), default="Medium", nullable=False)  # Low/Medium/High/Critical
    status = Column(String(20), default="Planning", nullable=False)  # Planning/Active/On Hold/Completed/Archived
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    archived_at = Column(DateTime(timezone=True), nullable=True)

    manager = relationship("User", foreign_keys=[manager_id])
    members = relationship(
        "ProjectMember", back_populates="project", cascade="all, delete-orphan"
    )
    documents = relationship("ProjectDocument", back_populates="project", cascade="all, delete-orphan")
    risks = relationship("Risk", back_populates="project", cascade="all, delete-orphan")
    blockers = relationship("Blocker", back_populates="project", cascade="all, delete-orphan")
    tasks = relationship("Task", back_populates="project", cascade="all, delete-orphan")
    insights = relationship("ProjectInsight", back_populates="project", cascade="all, delete-orphan")
    ai_runs = relationship("AiRun", back_populates="project", cascade="all, delete-orphan")


class ProjectMember(Base):
    __tablename__ = "project_members"
    __table_args__ = (UniqueConstraint("project_id", "user_id", name="uq_project_member"),)

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    assigned_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    assigned_by = Column(Integer, ForeignKey("users.id"), nullable=True)

    project = relationship("Project", back_populates="members")
    user = relationship("User", foreign_keys=[user_id], back_populates="memberships")


class ProjectDocument(TimestampMixin, Base):
    __tablename__ = "documents"
    __table_args__ = (
        Index("ix_doc_project_status", "project_id", "status"),
    )

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False, index=True)
    file_name = Column(String(512), nullable=False)       # stored name on disk
    original_name = Column(String(512), nullable=False)   # user-uploaded name
    file_type = Column(String(20), nullable=False)        # pdf/docx/csv/txt
    mime_type = Column(String(200), default="")
    file_size = Column(Integer, default=0)                # bytes
    storage_path = Column(Text, nullable=False)
    uploaded_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    uploaded_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    status = Column(String(20), default="Uploaded", nullable=False)  # Uploaded/Processing/Processed/Failed
    error_message = Column(Text, nullable=True)
    page_count = Column(Integer, nullable=True)
    extracted_text = Column(Text, nullable=True)          # cleaned extracted content
    chunk_count = Column(Integer, default=0, nullable=False)
    embedding_status = Column(String(20), default="Not Started")  # Not Started/Processing/Completed/Failed
    content_hash = Column(String(64), nullable=True, index=True)  # sha256 for duplicate detection

    project = relationship("Project", back_populates="documents")
    uploader = relationship("User", foreign_keys=[uploaded_by], overlaps="uploaded_documents")
    chunks = relationship(
        "DocumentChunk", back_populates="document", cascade="all, delete-orphan"
    )
    source_risks = relationship("Risk", back_populates="source_document")
    source_tasks = relationship("Task", back_populates="source_document")


class DocumentChunk(Base):
    __tablename__ = "document_chunks"
    __table_args__ = (
        Index("ix_chunk_project", "project_id"),
        Index("ix_chunk_doc", "document_id"),
    )

    id = Column(Integer, primary_key=True)
    document_id = Column(Integer, ForeignKey("documents.id"), nullable=False, index=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False)
    chunk_index = Column(Integer, nullable=False)
    text = Column(Text, nullable=False)
    page_number = Column(Integer, nullable=True)
    section = Column(String(255), nullable=True)
    row_number = Column(Integer, nullable=True)
    char_start = Column(Integer, nullable=True)
    char_end = Column(Integer, nullable=True)
    vector_id = Column(String(128), nullable=True)  # chroma id

    document = relationship("ProjectDocument", back_populates="chunks")


class Risk(Base):
    __tablename__ = "risks"

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False, index=True)
    title = Column(String(255), nullable=False)
    description = Column(Text, default="")
    severity = Column(String(20), default="Medium", nullable=False)  # Low/Medium/High/Critical
    probability = Column(String(20), default="Medium", nullable=False)  # Low/Medium/High
    impact = Column(String(20), default="Medium", nullable=False)       # Low/Medium/High
    evidence = Column(Text, default="")
    source_document_id = Column(Integer, ForeignKey("documents.id"), nullable=True)
    recommended_action = Column(Text, default="")
    status = Column(String(20), default="Open", nullable=False)  # Open/In Progress/Mitigated/Closed
    source_type = Column(String(20), default="ai_detected", nullable=False)  # ai_detected/manual
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    project = relationship("Project", back_populates="risks")
    source_document = relationship("ProjectDocument", back_populates="source_risks")


class Blocker(Base):
    __tablename__ = "blockers"

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False, index=True)
    title = Column(String(255), nullable=False)
    description = Column(Text, default="")
    severity = Column(String(20), default="Medium", nullable=False)
    owner = Column(String(255), nullable=True)
    identified_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    expected_resolution = Column(String(255), nullable=True)
    status = Column(String(20), default="Open", nullable=False)  # Open/In Progress/Resolved
    source_type = Column(String(20), default="ai_detected", nullable=False)  # ai_detected/employee_reported
    evidence = Column(Text, default="")
    source_document_id = Column(Integer, ForeignKey("documents.id"), nullable=True)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    project = relationship("Project", back_populates="blockers")
    source_document = relationship("ProjectDocument", foreign_keys=[source_document_id])


class Task(Base):
    __tablename__ = "tasks"
    __table_args__ = (
        Index("ix_task_project_status", "project_id", "status"),
        Index("ix_task_assigned", "assigned_to"),
    )

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False, index=True)
    title = Column(String(255), nullable=False)
    description = Column(Text, default="")
    assigned_to = Column(Integer, ForeignKey("users.id"), nullable=True)
    due_date = Column(DateTime(timezone=True), nullable=True)
    priority = Column(String(20), default="Medium", nullable=False)  # Low/Medium/High/Critical
    status = Column(String(20), default="Pending", nullable=False)   # Pending/In Progress/Completed/Blocked
    source_type = Column(String(20), default="manual", nullable=False)  # ai_generated/manual
    source_document_id = Column(Integer, ForeignKey("documents.id"), nullable=True)
    source_ref = Column(String(512), default="")  # e.g. "Meeting_2026-09-20.docx — Page 2"
    ai_generated = Column(Boolean, default=False, nullable=False)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    project = relationship("Project", back_populates="tasks")
    assignee = relationship("User", foreign_keys=[assigned_to])
    source_document = relationship("ProjectDocument", back_populates="source_tasks")


class ProjectInsight(Base):
    __tablename__ = "project_insights"

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False, index=True)
    agent = Column(String(50), nullable=False)        # scope/risk/forecast/blocker/action/health
    category = Column(String(100), nullable=False)    # e.g. deliverable, risk_high, ...
    title = Column(String(255), default="")
    summary = Column(Text, default="")
    payload = Column(JSON, default=dict)              # structured content
    evidence = Column(JSON, default=list)             # [{document, page, section, quote}]
    ai_run_id = Column(Integer, ForeignKey("ai_runs.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    project = relationship("Project", back_populates="insights")
    ai_run = relationship("AiRun", back_populates="insights")


class AiRun(Base):
    __tablename__ = "ai_runs"

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False, index=True)
    agent = Column(String(50), nullable=False)        # e.g. scope, risk, forecast, blocker, action, orchestrator
    provider = Column(String(100), default="")
    model = Column(String(100), default="")
    status = Column(String(20), default="Running", nullable=False)  # Running/Completed/Failed
    error = Column(Text, nullable=True)
    started_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    ended_at = Column(DateTime(timezone=True), nullable=True)
    source_document_ids = Column(JSON, default=list)
    result_metadata = Column(JSON, default=dict)

    project = relationship("Project", back_populates="ai_runs")
    insights = relationship("ProjectInsight", back_populates="ai_run")


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    user_email = Column(String(255), default="")
    action = Column(String(100), nullable=False, index=True)
    resource_type = Column(String(50), default="")
    resource_id = Column(String(64), default="")
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=True)
    detail = Column(Text, default="")
    ip_address = Column(String(64), default="")
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False, index=True)


class SystemSetting(Base):
    __tablename__ = "system_settings"

    key = Column(String(100), primary_key=True)
    value = Column(Text, default="")
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)