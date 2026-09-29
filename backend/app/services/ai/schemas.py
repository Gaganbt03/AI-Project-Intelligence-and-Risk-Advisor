from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator


class SourceRef(BaseModel):
    document: str = ""          # original file name
    page: Optional[int] = None
    section: Optional[str] = None
    row: Optional[int] = None
    quote: str = ""


def _ok(val: str) -> str:
    return (val or "").strip()


class ScopeItem(BaseModel):
    text: str = ""
    evidence: Optional[SourceRef] = None


class MilestoneItem(BaseModel):
    name: str = ""
    date: Optional[str] = None
    evidence: Optional[SourceRef] = None


class ScopeSchema(BaseModel):
    project_goal: str = ""
    scope: list[str] = Field(default_factory=list)
    out_of_scope: list[str] = Field(default_factory=list)
    deliverables: list[str] = Field(default_factory=list)
    milestones: list[MilestoneItem] = Field(default_factory=list)
    timeline: list[str] = Field(default_factory=list)
    responsibilities: list[str] = Field(default_factory=list)
    technologies: list[str] = Field(default_factory=list)
    requirements: list[str] = Field(default_factory=list)

    @field_validator("project_goal")
    @classmethod
    def clean_goal(cls, v: str) -> str:
        return _ok(v)


class RiskSchema(BaseModel):
    title: str = ""
    description: str = ""
    severity: str = "Medium"
    probability: str = "Medium"
    impact: str = "Medium"
    evidence: str = ""
    source_document: str = ""
    source_page: Optional[int] = None
    source_section: Optional[str] = None
    recommended_action: str = ""

    @field_validator("severity", "probability", "impact")
    @classmethod
    def cap_level(cls, v: str) -> str:
        allowed = {"LOW", "MEDIUM", "HIGH", "CRITICAL"}
        u = (v or "").upper()
        if u not in allowed and u != "CRITICAL":
            u = "MEDIUM" if u not in allowed else u
        return u if u in allowed else "MEDIUM"


class BlockerSchema(BaseModel):
    title: str = ""
    description: str = ""
    severity: str = "Medium"
    owner: str = ""
    evidence: str = ""
    source_document: str = ""
    source_page: Optional[int] = None
    source_section: Optional[str] = None
    recommended_action: str = ""

    @field_validator("severity")
    @classmethod
    def cap_severity(cls, v: str) -> str:
        u = (v or "").upper()
        return u if u in {"LOW", "MEDIUM", "HIGH", "CRITICAL"} else "MEDIUM"


class ActionItemSchema(BaseModel):
    action: str = ""
    assigned_person: str = ""
    deadline: Optional[str] = None
    priority: str = "Medium"
    source_document: str = ""
    source_page: Optional[int] = None
    source_section: Optional[str] = None

    @field_validator("priority")
    @classmethod
    def cap_priority(cls, v: str) -> str:
        u = (v or "").upper()
        return u if u in {"LOW", "MEDIUM", "HIGH", "CRITICAL"} else "MEDIUM"


class ForecastFactor(BaseModel):
    factor: str = ""
    impact: str = ""
    severity: str = "Medium"

    @field_validator("severity")
    @classmethod
    def cap(cls, v: str) -> str:
        u = (v or "").upper()
        return u if u in {"LOW", "MEDIUM", "HIGH", "CRITICAL"} else "MEDIUM"


class ForecastSchema(BaseModel):
    current_status: str = ""
    schedule_status: str = ""          # On Track / Minor Risk / Significant Risk / At Risk
    expected_delivery: str = ""
    risk_level: str = "Medium"
    factors: list[ForecastFactor] = Field(default_factory=list)
    evidence: list[SourceRef] = Field(default_factory=list)
    caveat: str = "Forecast is an AI analysis, not a precise prediction."

    @field_validator("risk_level")
    @classmethod
    def cap(cls, v: str) -> str:
        u = (v or "").upper()
        return u if u in {"LOW", "MEDIUM", "HIGH", "CRITICAL"} else "MEDIUM"


class ScopeAgentResult(BaseModel):
    project_goal: str = ""
    scope: list[str] = Field(default_factory=list)
    out_of_scope: list[str] = Field(default_factory=list)
    deliverables: list[str] = Field(default_factory=list)
    milestones: list[MilestoneItem] = Field(default_factory=list)
    timeline: list[str] = Field(default_factory=list)
    responsibilities: list[str] = Field(default_factory=list)
    technologies: list[str] = Field(default_factory=list)
    requirements: list[str] = Field(default_factory=list)


class RiskAgentResult(BaseModel):
    risks: list[RiskSchema] = Field(default_factory=list)


class BlockerAgentResult(BaseModel):
    blockers: list[BlockerSchema] = Field(default_factory=list)


class ActionItemAgentResult(BaseModel):
    action_items: list[ActionItemSchema] = Field(default_factory=list)


# Helper: repair levels into allowed tokens
def normalize_level(value: str, allowed: list[str]) -> str:
    u = (value or "").strip().upper()
    if u not in allowed:
        return allowed[1]
    return u


class CategoryInsight(BaseModel):
    """Generic structured insight row saved by orchestrator."""
    id: Optional[Any] = None
    category: str = ""
    title: str = ""
    summary: str = ""
    payload: dict = Field(default_factory=dict)
    evidence: list[SourceRef] = Field(default_factory=list)