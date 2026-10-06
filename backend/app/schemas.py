from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field


# ---------- Enums / constants ----------

PROJECT_STATUSES = ["Planning", "Active", "On Hold", "Completed", "Archived"]
PROJECT_PRIORITIES = ["Low", "Medium", "High", "Critical"]
SEVERITIES = ["Low", "Medium", "High", "Critical"]
DOCTYPES = {"pdf": ".pdf", "docx": ".docx", "csv": ".csv", "txt": ".txt"}


# ---------- Common ----------

class ApiResponse(BaseModel):
    ok: bool = True
    message: str = ""


class ErrorResponse(BaseModel):
    ok: bool = False
    error: str
    detail: Optional[str] = None


# ---------- Auth ----------

class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1)


class UserSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    email: str
    name: str
    is_active: bool = True


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserSummary


class SetupAdminRequest(BaseModel):
    """First-account creation payload.

    The request name is retained for wire compatibility; it carries no role.
    """

    name: str = Field(min_length=1, max_length=255)
    email: EmailStr
    password: str = Field(min_length=8, max_length=200)


# ---------- Users ----------

class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    email: str
    name: str
    is_active: bool
    created_at: Optional[datetime] = None
    last_login: Optional[datetime] = None
    project_ids: list[int] = []


class UserCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    email: EmailStr
    password: str = Field(min_length=8, max_length=200)
    project_ids: list[int] = []


class UserUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    password: Optional[str] = Field(default=None, min_length=8, max_length=200)
    is_active: Optional[bool] = None


class UserProjectsUpdate(BaseModel):
    project_ids: list[int] = []


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str = Field(min_length=8, max_length=200)


# ---------- Projects ----------

class ProjectMemberOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    email: str
    assigned_at: Optional[datetime] = None


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    description: str = ""
    objective: str = ""
    manager_id: Optional[int] = None
    manager_name: Optional[str] = None
    start_date: Optional[datetime] = None
    expected_end_date: Optional[datetime] = None
    priority: str = "Medium"
    status: str = "Planning"
    created_at: Optional[datetime] = None
    member_count: int = 0
    document_count: int = 0
    task_count: int = 0
    member_ids: list[int] = []


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str = ""
    objective: str = ""
    manager_id: Optional[int] = None
    start_date: Optional[datetime] = None
    expected_end_date: Optional[datetime] = None
    priority: str = Field(default="Medium", pattern=f"^({'|'.join(PROJECT_PRIORITIES)})$")
    status: str = Field(default="Planning", pattern=f"^({'|'.join(PROJECT_STATUSES)})$")
    member_ids: list[int] = []


class ProjectUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    description: Optional[str] = None
    objective: Optional[str] = None
    manager_id: Optional[int] = None
    start_date: Optional[datetime] = None
    expected_end_date: Optional[datetime] = None
    priority: Optional[str] = Field(default=None, pattern=f"^({'|'.join(PROJECT_PRIORITIES)})$")
    status: Optional[str] = Field(default=None, pattern=f"^({'|'.join(PROJECT_STATUSES)})$")
    member_ids: Optional[list[int]] = None


# ---------- Documents ----------

class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    project_id: int
    project_name: str = ""
    file_name: str = ""
    original_name: str = ""
    file_type: str = ""
    file_size: int = 0
    uploaded_by: Optional[int] = None
    uploader_name: str = ""
    uploaded_at: Optional[datetime] = None
    status: str = "Uploaded"
    error_message: Optional[str] = None
    page_count: Optional[int] = None
    chunk_count: int = 0
    embedding_status: str = "Not Started"


class DashboardSummary(BaseModel):
    total_projects: int = 0
    active_projects: int = 0
    team_members: int = 0
    documents: int = 0
    open_risks: int = 0
    critical_risks: int = 0
    open_blockers: int = 0
    avg_health: Optional[float] = None
    health_metric_count: int = 0


# ---------- AI schema import for reuse ----------

from app.services.ai.schemas import (  # noqa: E402
    ActionItemSchema,
    BlockerSchema,
    ForecastSchema,
    RiskSchema,
    ScopeSchema,
)

__all__ = [
    "ApiResponse", "ErrorResponse", "LoginRequest", "LoginResponse", "SetupAdminRequest",
    "UserOut", "UserCreate", "UserUpdate", "UserProjectsUpdate", "ChangePasswordRequest",
    "ProjectOut", "ProjectCreate", "ProjectUpdate", "ProjectMemberOut",
    "DocumentOut", "DashboardSummary",
    "ScopeSchema", "RiskSchema", "ForecastSchema", "BlockerSchema", "ActionItemSchema",
]