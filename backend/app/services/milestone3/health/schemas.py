"""Health scoring schemas and the documented formula constants."""

from __future__ import annotations

FORMULA_VERSION = "m3.2"

# ---------------------------------------------------------------------------
# The Milestone 3 health formula — deterministic, versioned, no LLM input.
#
#   overall = round( SUM( score_i * weight_i ) / SUM( weight_i ) )
#
# over the dimensions the project actually has data for. Every dimension
# returns a score in [0, 100] where 100 is healthiest. Dimensions with no
# supporting data are marked ``supported: false``, are excluded from the
# weighted average, and are listed in ``skipped_dimensions`` so the UI can say
# exactly why they are missing.
# ---------------------------------------------------------------------------

#: Dimension key -> (label, weight, required)
#:
#: The first three are the specification's required dimensions and carry 20
#: points each; the three optional dimensions carry 15, 15 and 10. The weights
#: sum to 100, so a fully supported project is scored on the published
#: percentage points directly. They are still normalised by
#: ``SUM(weight)`` over the *supported* dimensions only, so a project that is
#: missing an optional dimension is not punished for it.
#:
#: Required dimensions are always computed; where the underlying data is missing
#: they score on the documented "no data" rule rather than being dropped, so a
#: project can never hide a problem by lacking data.
DIMENSIONS: tuple[tuple[str, str, float, bool], ...] = (
    ("scope_clarity", "Scope Clarity", 20.0, True),
    ("timeline_risk", "Timeline Risk", 20.0, True),
    ("blocker_count", "Blocker Count", 20.0, True),
    ("risk_exposure", "Risk Exposure", 15.0, False),
    ("deliverable_progress", "Deliverable Progress", 15.0, False),
    ("documentation_completeness", "Documentation Completeness", 10.0, False),
)

REQUIRED_DIMENSIONS = tuple(k for k, _, _, req in DIMENSIONS if req)
OPTIONAL_DIMENSIONS = tuple(k for k, _, _, req in DIMENSIONS if not req)

#: Health status bands, highest band first.
STATUS_BANDS: tuple[tuple[float, str], ...] = (
    (85.0, "Excellent"),
    (70.0, "Healthy"),
    (55.0, "Watch"),
    (40.0, "At Risk"),
    (0.0, "Critical"),
)

#: Percentage of tasks past their due date that drives the timeline penalty.
OVERDUE_PENALTY_PER_TASK = 4.0
OVERDUE_PENALTY_CAP = 20.0

#: A project with fewer than this many documents is treated as under-documented.
MIN_DOCUMENTS_FOR_COMPLETENESS = 3

#: Below this retrieval-free evidence threshold the Documentation section
#: reports insufficient evidence rather than a score.
MIN_DOCUMENTED_DELIVERABLES = 1


def status_for(score: float) -> str:
    for threshold, label in STATUS_BANDS:
        if score >= threshold:
            return label
    return "Critical"


def dimension_label(key: str) -> str:
    for k, label, _, _ in DIMENSIONS:
        if k == key:
            return label
    return key


# Human-readable formula, surfaced verbatim in the API response and the UI so
# the scoring is never a black box.
FORMULA_TEXT = {
    "version": FORMULA_VERSION,
    "overall": (
        "overall = round( SUM( score_i * weight_i ) / SUM( weight_i ) ) over every "
        "supported dimension, floored at 0 and capped at 100. The six weights "
        "(20, 20, 20, 15, 15, 10) sum to 100, so a project supported on all six "
        "dimensions is scored directly on those published percentage points."
    ),
    "weights": {
        "scope_clarity": 20.0,
        "timeline_risk": 20.0,
        "blocker_count": 20.0,
        "risk_exposure": 15.0,
        "deliverable_progress": 15.0,
        "documentation_completeness": 10.0,
    },
    "status_bands": ">=85 Excellent | >=70 Healthy | >=55 Watch | >=40 At Risk | <40 Critical",
    "dimensions": {
        "scope_clarity": {
            "weight": 20.0,
            "required": True,
            "formula": (
                "100 * (0.25*goal + 0.20*scope + 0.20*deliverables + 0.15*milestones "
                "+ 0.10*timeline + 0.10*responsibilities), where each factor is "
                "min(1, count/target) and targets are goal>=1, scope>=5, "
                "deliverables>=5, milestones>=3, timeline>=1, responsibilities>=2. "
                "With no scope analysis at all the dimension scores 0."
            ),
        },
        "timeline_risk": {
            "weight": 20.0,
            "required": True,
            "formula": (
                "100 * (0.30*schedule + 0.20*plan + 0.20*pace + 0.15*overdue + 0.15*blocker_impact); "
                "schedule = mapped schedule_status (On Track 1.0, Minor Risk 0.72, "
                "Significant Risk 0.45, At Risk 0.22, unknown 0.0); plan = both project dates "
                "present and end > start; pace = min(1, completion_rate/0.6); overdue = "
                "max(0, 1 - 0.04*overdue_tasks) capped at 0.8; blocker_impact = "
                "max(0, 1 - 0.15*open_critical_or_high_blockers). A lower score means a "
                "HIGHER timeline risk."
            ),
        },
        "blocker_count": {
            "weight": 20.0,
            "required": True,
            "formula": (
                "100 * max(0, 1 - weighted_open_blockers / 5) with Critical=3, High=2, "
                "Medium=1, Low=0.5; resolved/closed blockers do not count."
            ),
        },
        "risk_exposure": {
            "weight": 15.0,
            "required": False,
            "formula": (
                "100 * max(0, 1 - total_open_risk_score / 60) where each open risk scores "
                "severity x probability x impact (1-25) and mitigated/closed risks are "
                "excluded. Unsupported when the project has no risk records at all."
            ),
        },
        "deliverable_progress": {
            "weight": 15.0,
            "required": False,
            "formula": (
                "100 * 0.6*completion_rate + 0.2*deliverable_documentation + 0.2*task_health; "
                "completion_rate = completed/total tasks; deliverable_documentation = "
                "min(1, documented_deliverables / 5) where a deliverable is documented when "
                "a matching scope insight exists; task_health = 1 - 0.2*blocked_tasks/total. "
                "Unsupported when the project has no tasks."
            ),
        },
        "documentation_completeness": {
            "weight": 10.0,
            "required": False,
            "formula": (
                "100 * (0.5*processed_ratio + 0.3*vector_ratio + 0.2*type_coverage); "
                "processed_ratio = processed/total documents, vector_ratio = documents with "
                "chunks / total documents, type_coverage = distinct file types / 4 "
                "(pdf, docx, csv, txt). Unsupported when the project has no documents."
            ),
        },
    },
    "notes": [
        "Every input is a value already stored in the database. "
        "Identical project data always yields an identical score.",
        "The LLM is given the finished numbers and may only write prose that "
        "explains them. It never contributes to the arithmetic.",
    ],
}

__all__ = [
    "FORMULA_VERSION",
    "FORMULA_TEXT",
    "DIMENSIONS",
    "REQUIRED_DIMENSIONS",
    "OPTIONAL_DIMENSIONS",
    "STATUS_BANDS",
    "OVERDUE_PENALTY_PER_TASK",
    "OVERDUE_PENALTY_CAP",
    "MIN_DOCUMENTS_FOR_COMPLETENESS",
    "MIN_DOCUMENTED_DELIVERABLES",
    "status_for",
    "dimension_label",
]
