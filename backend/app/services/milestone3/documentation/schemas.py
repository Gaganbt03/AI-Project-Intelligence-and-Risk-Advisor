"""Schemas for the Documentation Generation Agent.

Three responsibilities live here:

1. **LLM output shape** — only the user story section asks the model for
   structured content, and even then every field is optional: a missing field
   becomes ``""`` and the renderer substitutes a placeholder rather than
   inventing a value.
2. **Structured artifacts** — the strongly-typed ``UserStoryArtifact`` /
   ``RiskArtifact`` / ``ActionArtifact`` shapes the UI renders and the DOCX
   exporter writes. These are what the specification requires the user to see,
   as opposed to a single block of Markdown.
3. **Validation vocabulary** — the ``SUPPORTED`` / ``UNSUPPORTED`` / ``MISSING``
   / ``CONFLICT`` verdicts and the summary block reported to the user.

The risk register and the action item list are built deterministically from
existing ``Risk`` / ``Task`` / ``Blocker`` rows plus document-grounded fields, so
they need no LLM schema at all — which is exactly what keeps them free of
duplicates and fabrications.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator

#: Used when the documents state no acceptance criteria at all.
NO_CRITERIA = "No acceptance criteria explicitly specified in project data."


class EvidenceItem(BaseModel):
    """A citation back into an uploaded project document."""

    document: str = ""
    page: Optional[int] = None
    section: Optional[str] = None
    row: Optional[int] = None
    quote: str = ""

    def citation(self) -> str:
        parts = [self.document or "Unknown document"]
        if self.page is not None:
            parts.append(f"Page {self.page}")
        if self.section:
            parts.append(self.section)
        if self.row is not None:
            parts.append(f"Row {self.row}")
        return " — ".join(p for p in parts if p)


# --------------------------------------------------------------------------- #
# LLM output schemas (user stories only)
# --------------------------------------------------------------------------- #


class UserStory(BaseModel):
    """One story as returned by the model.

    ``role``/``goal``/``benefit`` map to the canonical "As a / I want / so that"
    sentence. ``role`` and ``benefit`` are deliberately *not* required: the
    specification forbids inventing an actor or a benefit, so an absent actor is
    reported as "Not specified in project data." rather than guessed.
    """

    title: str = ""
    role: str = ""
    goal: str = ""
    benefit: str = ""
    acceptance_criteria: list[str] = Field(default_factory=list)
    priority: str = ""
    related_requirement: str = ""
    status: str = ""
    source_document: str = ""
    evidence: str = ""

    @field_validator("priority")
    @classmethod
    def _cap_priority(cls, v: str) -> str:
        u = (v or "").strip().upper()
        return u if u in {"LOW", "MEDIUM", "HIGH", "CRITICAL"} else ""

    def is_substantive(self) -> bool:
        """A story needs a requirement to be real.

        An actor is *not* required: a system requirement with no stated actor is
        still a real requirement, and the artifact reports the actor as
        unspecified instead of inventing one.
        """
        return bool(self.goal.strip())


class UserStoryResult(BaseModel):
    user_stories: list[UserStory] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Validation layer
# --------------------------------------------------------------------------- #


class FieldVerdict(str, Enum):
    """Per-field outcome of the validation layer."""

    SUPPORTED = "SUPPORTED"
    UNSUPPORTED = "UNSUPPORTED"
    MISSING = "MISSING"
    CONFLICT = "CONFLICT"


class FieldFinding(BaseModel):
    """One validation verdict.

    ``value`` is the value actually published (post-correction). ``note`` is a
    short, factual reason — never chain-of-thought, never an explanation of how
    the validator reached the verdict.
    """

    field: str
    verdict: FieldVerdict = FieldVerdict.MISSING
    value: str = ""
    source: str = ""
    note: str = ""


class ValidationSummary(BaseModel):
    """The concise validation block surfaced in the UI and the DOCX header."""

    status: str = "Verified"
    fields_checked: int = 0
    supported: int = 0
    corrected: int = 0
    not_specified: int = 0
    unsupported_removed: int = 0
    conflicts: int = 0
    findings: list[FieldFinding] = Field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "fields_checked": self.fields_checked,
            "supported": self.supported,
            "corrected": self.corrected,
            "not_specified": self.not_specified,
            "unsupported_removed": self.unsupported_removed,
            "conflicts": self.conflicts,
            "findings": [f.model_dump() for f in self.findings],
        }


# --------------------------------------------------------------------------- #
# Structured artifacts (what the user sees and downloads)
# --------------------------------------------------------------------------- #


class SourceRef(BaseModel):
    """Where a field came from. Always one of the accepted provenance kinds."""

    document: str = ""
    evidence: str = ""
    page: Optional[int] = None
    section: str = ""
    row: Optional[int] = None

    def citation(self) -> str:
        parts = [self.document or "Unknown document"]
        if self.page is not None:
            parts.append(f"Page {self.page}")
        if self.section:
            parts.append(self.section)
        if self.row is not None:
            parts.append(f"Row {self.row}")
        return " — ".join(p for p in parts if p)


class DateTrace(BaseModel):
    """Admin value, document value, effective value and the reason for the pick."""

    effective_date: str = ""
    admin_date: str = ""
    document_date: str = ""
    source: str = ""
    evidence: str = ""
    document: str = ""
    conflict: bool = False
    vague_in_source: bool = False


class FieldStatuses(BaseModel):
    """Per-field verdicts for one artifact. Keys are the field names the
    specification asks the validator to check."""

    verdicts: dict[str, FieldVerdict] = Field(default_factory=dict)
    notes: dict[str, str] = Field(default_factory=dict)

    def verdict(self, name: str) -> FieldVerdict:
        return self.verdicts.get(name, FieldVerdict.MISSING)


class UserStoryArtifact(BaseModel):
    """US-001, in the exact field order the specification requires."""

    story_id: str = ""
    title: str = ""
    as_a: str = ""
    i_want: str = ""
    so_that: str = ""
    priority: str = ""
    status: str = ""
    acceptance_criteria: list[str] = Field(default_factory=list)
    related_requirement: str = ""
    source_document: str = ""
    source_evidence: str = ""
    source: SourceRef = Field(default_factory=SourceRef)
    validation: FieldStatuses = Field(default_factory=FieldStatuses)


class RiskArtifact(BaseModel):
    """R001, in the exact field order the specification requires."""

    risk_id: str = ""
    title: str = ""
    description: str = ""
    category: str = ""
    probability: str = ""
    impact: str = ""
    risk_score: int = 0
    risk_score_max: int = 25
    severity: str = ""
    severity_band: str = ""
    status: str = ""
    owner: str = ""
    mitigation: str = ""
    contingency: str = ""
    related_blockers: list[str] = Field(default_factory=list)
    related_actions: list[str] = Field(default_factory=list)
    source_document: str = ""
    source_evidence: str = ""
    source: SourceRef = Field(default_factory=SourceRef)
    conflicts: list[str] = Field(default_factory=list)
    validation: FieldStatuses = Field(default_factory=FieldStatuses)


class ActionArtifact(BaseModel):
    """ACT-001 / ACT-BLK-0002, in the exact field order the specification requires."""

    action_id: str = ""
    action: str = ""
    description: str = ""
    owner: str = ""
    priority: str = ""
    due_date: str = ""
    status: str = ""
    related_risk: str = ""
    related_blocker: str = ""
    source_document: str = ""
    source_evidence: str = ""
    origin: str = ""
    source: SourceRef = Field(default_factory=SourceRef)
    date_trace: DateTrace = Field(default_factory=DateTrace)
    validation: FieldStatuses = Field(default_factory=FieldStatuses)


class DocumentationContext(BaseModel):
    """Everything the renderer used, exposed for auditing and for the UI's
    "where did this come from" panel."""

    evidence: list[EvidenceItem] = Field(default_factory=list)
    has_documents: bool = False
    document_count: int = 0
    source_agents: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


__all__ = [
    "NO_CRITERIA",
    "ActionArtifact",
    "DateTrace",
    "DocumentationContext",
    "EvidenceItem",
    "FieldFinding",
    "FieldStatuses",
    "FieldVerdict",
    "RiskArtifact",
    "SourceRef",
    "UserStory",
    "UserStoryArtifact",
    "UserStoryResult",
    "ValidationSummary",
]
