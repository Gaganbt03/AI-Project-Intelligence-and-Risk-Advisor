"""Deterministic Project Health Scoring Module (Milestone 3.2).

Pure function of the stored project data
---------------------------------------
:func:`score_project` reads only rows that already exist (projects, documents,
document_chunks, tasks, risks, blockers, project_insights) and applies the
published arithmetic in :mod:`app.services.milestone3.health.schemas`. There is
no model call, no randomness and no wall-clock dependence anywhere in the score
path, so:

    score_project(state) == score_project(state)   # always

Calling it twice, or on two different machines with the same data, returns the
same numbers. The optional narrative in :func:`explain_health` is generated
*afterwards* from the finished numbers and cannot alter them.

Three tiers of function live here:

* ``collect_*``  — read real inputs into an :class:`HealthInputs` bundle
* ``score_*``    — the pure arithmetic, one function per dimension
* ``build_report`` — assembles the API/UI payload including explanations
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models import (
    Blocker,
    Project,
    ProjectDocument,
    ProjectInsight,
    Risk,
    Task,
)
from app.services.milestone3.common import (
    NOT_SPECIFIED,
    SCHEDULE_STATUS_SCORE,
    level,
    severity_score,
)
from app.services.milestone3.health.schemas import (
    DIMENSIONS,
    FORMULA_TEXT,
    FORMULA_VERSION,
    MIN_DOCUMENTED_DELIVERABLES,
    OPTIONAL_DIMENSIONS,
    OVERDUE_PENALTY_CAP,
    OVERDUE_PENALTY_PER_TASK,
    dimension_label,
    status_for,
)

logger = logging.getLogger("m3.health")

OPEN_STATUSES = ("Open", "In Progress")
CLOSED_TASK = "Completed"

#: Risk score above which a risk is treated as "major" in the explanation.
MAJOR_RISK_THRESHOLD = 12

#: Recency bucket boundaries (days) for schedule pressure, in days.
_MILESTONE_SOON_DAYS = 30


# --------------------------------------------------------------------------- #
# Input collection
# --------------------------------------------------------------------------- #


@dataclass
class HealthInputs:
    """Every value the formula consumes, captured verbatim from the database."""

    project_id: int
    project_name: str
    project_status: str
    project_priority: str
    project_objective: str
    start_date: datetime | None
    end_date: datetime | None

    documents_total: int = 0
    documents_processed: int = 0
    documents_with_chunks: int = 0
    document_types: set[str] = field(default_factory=set)
    total_chunks: int = 0

    tasks_total: int = 0
    tasks_completed: int = 0
    tasks_blocked: int = 0
    tasks_overdue: int = 0
    tasks_with_due_date: int = 0

    risks_total: int = 0
    risks_open: int = 0
    risks_mitigated: int = 0
    risk_score_total: int = 0
    major_risks: list[dict] = field(default_factory=list)
    risks_by_severity: dict[str, int] = field(default_factory=dict)

    blockers_total: int = 0
    blockers_open: int = 0
    blockers_weighted: float = 0.0
    open_blockers: list[dict] = field(default_factory=list)

    schedule_status: str = ""
    forecast_risk_level: str = ""
    forecast_expected_delivery: str = ""

    scope_goal: str = ""
    scope_items: list[str] = field(default_factory=list)
    deliverables: list[str] = field(default_factory=list)
    milestones: list[dict] = field(default_factory=list)
    timeline: list[str] = field(default_factory=list)
    responsibilities: list[str] = field(default_factory=list)
    requirements: list[str] = field(default_factory=list)

    has_scope_analysis: bool = False
    documented_deliverables: int = 0
    last_analysis_at: datetime | None = None

    def as_dict(self) -> dict:
        """The exact inputs used — stored on the snapshot for reproducibility."""
        return {
            "project_id": self.project_id,
            "project_name": self.project_name,
            "project_status": self.project_status,
            "project_priority": self.project_priority,
            "has_objective": bool(self.project_objective),
            "start_date": self.start_date.isoformat() if self.start_date else None,
            "end_date": self.end_date.isoformat() if self.end_date else None,
            "documents_total": self.documents_total,
            "documents_processed": self.documents_processed,
            "documents_with_chunks": self.documents_with_chunks,
            "document_types": sorted(self.document_types),
            "total_chunks": self.total_chunks,
            "tasks_total": self.tasks_total,
            "tasks_completed": self.tasks_completed,
            "tasks_blocked": self.tasks_blocked,
            "tasks_overdue": self.tasks_overdue,
            "risks_total": self.risks_total,
            "risks_open": self.risks_open,
            "risks_mitigated": self.risks_mitigated,
            "risk_score_total": self.risk_score_total,
            "risks_by_severity": dict(self.risks_by_severity),
            "blockers_total": self.blockers_total,
            "blockers_open": self.blockers_open,
            "blockers_weighted": self.blockers_weighted,
            "schedule_status": self.schedule_status,
            "forecast_risk_level": self.forecast_risk_level,
            "scope_counts": {
                "goal": 1 if self.scope_goal else 0,
                "scope": len(self.scope_items),
                "deliverables": len(self.deliverables),
                "milestones": len(self.milestones),
                "timeline": len(self.timeline),
                "responsibilities": len(self.responsibilities),
                "requirements": len(self.requirements),
            },
            "has_scope_analysis": self.has_scope_analysis,
            "documented_deliverables": self.documented_deliverables,
        }


_BLOCKER_WEIGHT = {"CRITICAL": 3.0, "HIGH": 2.0, "MEDIUM": 1.0, "LOW": 0.5}


def collect_inputs(db: Session, project: Project, now: datetime | None = None) -> HealthInputs:
    """Read every value the formula needs out of the existing tables."""
    now = now or datetime.now(timezone.utc)
    pid = project.id
    data = HealthInputs(
        project_id=pid,
        project_name=project.name,
        project_status=project.status or "",
        project_priority=project.priority or "",
        project_objective=(project.objective or "").strip(),
        start_date=_aware(project.start_date),
        end_date=_aware(project.expected_end_date),
    )

    # --- documents ------------------------------------------------------- #
    docs = db.query(ProjectDocument).filter(ProjectDocument.project_id == pid).all()
    data.documents_total = len(docs)
    data.documents_processed = sum(1 for d in docs if d.status == "Processed")
    data.documents_with_chunks = sum(1 for d in docs if (d.chunk_count or 0) > 0)
    data.total_chunks = sum(int(d.chunk_count or 0) for d in docs)
    data.document_types = {(d.file_type or "").lower() for d in docs if d.file_type}

    # --- tasks ----------------------------------------------------------- #
    tasks = db.query(Task).filter(Task.project_id == pid).all()
    data.tasks_total = len(tasks)
    data.tasks_completed = sum(1 for t in tasks if t.status == CLOSED_TASK)
    data.tasks_blocked = sum(1 for t in tasks if t.status == "Blocked")
    for t in tasks:
        due = _aware(t.due_date)
        if due is None:
            continue
        data.tasks_with_due_date += 1
        if t.status != CLOSED_TASK and due < now:
            data.tasks_overdue += 1

    # --- risks ----------------------------------------------------------- #
    risks = db.query(Risk).filter(Risk.project_id == pid).all()
    data.risks_total = len(risks)
    by_sev: dict[str, int] = {}
    for r in risks:
        sev = (r.severity or "").strip().title() or "Medium"
        by_sev[sev] = by_sev.get(sev, 0) + 1
        if r.status in OPEN_STATUSES:
            data.risks_open += 1
            score = severity_score(r.severity, r.probability, r.impact)
            data.risk_score_total += score
            if score >= MAJOR_RISK_THRESHOLD:
                data.major_risks.append(
                    {
                        "id": r.id,
                        "title": r.title,
                        "severity": sev,
                        "score": score,
                        "status": r.status,
                        "evidence": (r.evidence or "").strip(),
                    }
                )
        else:
            data.risks_mitigated += 1
    data.risks_by_severity = by_sev
    data.major_risks.sort(key=lambda x: (-x["score"], x["id"]))

    # --- blockers -------------------------------------------------------- #
    blockers = db.query(Blocker).filter(Blocker.project_id == pid).all()
    data.blockers_total = len(blockers)
    for b in blockers:
        if b.status not in OPEN_STATUSES:
            continue
        data.blockers_open += 1
        w = _BLOCKER_WEIGHT.get(level(b.severity), 1.0)
        data.blockers_weighted += w
        data.open_blockers.append(
            {
                "id": b.id,
                "title": b.title,
                "severity": (b.severity or "").strip().title() or "Medium",
                "status": b.status,
                "owner": (b.owner or "").strip(),
                "weight": w,
                "evidence": (b.evidence or "").strip(),
            }
        )
    data.open_blockers.sort(key=lambda x: (-x["weight"], x["id"]))

    # --- Milestone 2 forecast ------------------------------------------- #
    forecast = (
        db.query(ProjectInsight)
        .filter(ProjectInsight.project_id == pid, ProjectInsight.agent == "forecast")
        .order_by(ProjectInsight.id.desc())
        .first()
    )
    if forecast:
        payload = forecast.payload or {}
        data.schedule_status = (payload.get("schedule_status") or "").strip()
        data.forecast_risk_level = (payload.get("risk_level") or "").strip()
        data.forecast_expected_delivery = (payload.get("expected_delivery") or "").strip()
        data.last_analysis_at = forecast.created_at

    # --- Milestone 2 scope ---------------------------------------------- #
    scope_rows = (
        db.query(ProjectInsight)
        .filter(ProjectInsight.project_id == pid, ProjectInsight.agent == "scope")
        .order_by(ProjectInsight.id.desc())
        .all()
    )
    data.has_scope_analysis = bool(scope_rows)
    seen: dict[str, set[str]] = {}
    for row in reversed(scope_rows):
        if row.category == "project_goal":
            if not data.scope_goal:
                data.scope_goal = (row.summary or row.title or "").strip()
            continue
        bucket = seen.setdefault(row.category, set())
        label = (row.title or row.summary or "").strip()
        if label:
            bucket.add(label)
    data.scope_items = sorted(seen.get("scope", set()))
    data.deliverables = sorted(seen.get("deliverable", set()))
    data.timeline = sorted(seen.get("timeline", set()))
    data.responsibilities = sorted(seen.get("responsibility", set()))
    data.requirements = sorted(seen.get("requirement", set()))
    for row in reversed(scope_rows):
        if row.category != "milestone":
            continue
        name = (row.title or "").strip()
        if not name:
            continue
        date = ""
        if isinstance(row.payload, dict):
            date = (row.payload.get("date") or "").strip()
        if not any(m["name"] == name for m in data.milestones):
            data.milestones.append({"name": name, "date": date})

    # A deliverable counts as documented when the scope agent produced a
    # matching entry. We deliberately do not consult the LLM here.
    data.documented_deliverables = min(
        len(data.deliverables), max(0, len(data.deliverables))
    )
    return data


def _aware(value: datetime | None) -> datetime | None:
    """SQLite hands back naive datetimes; treat them as UTC for comparisons."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


# --------------------------------------------------------------------------- #
# Pure scoring
# --------------------------------------------------------------------------- #


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _saturate(count: int, target: int) -> float:
    """min(1, count/target) with a zero target treated as unsatisfied."""
    if target <= 0:
        return 0.0
    return _clamp(count / target)


def score_scope_clarity(d: HealthInputs) -> dict:
    """Required. 100 = goal + scope + deliverables + milestones + timeline +
    responsibilities are all well documented by the Milestone 2 scope agent."""
    if not d.has_scope_analysis:
        return _dim(
            "scope_clarity",
            0.0,
            supported=True,
            detail="No scope analysis has been run for this project, so scope clarity cannot be demonstrated.",
            facts={
                "goal_documented": False,
                "scope_items": 0,
                "deliverables": 0,
                "milestones": 0,
            },
        )
    goal = 1.0 if d.scope_goal else 0.0
    parts = {
        "goal": (goal, 0.25),
        "scope": (_saturate(len(d.scope_items), 5), 0.20),
        "deliverables": (_saturate(len(d.deliverables), 5), 0.20),
        "milestones": (_saturate(len(d.milestones), 3), 0.15),
        "timeline": (_saturate(len(d.timeline), 1), 0.10),
        "responsibilities": (_saturate(len(d.responsibilities), 2), 0.10),
    }
    score = 100.0 * sum(value * weight for value, weight in parts.values())
    return _dim(
        "scope_clarity",
        score,
        supported=True,
        detail=_scope_detail(parts),
        facts={
            "goal_documented": bool(d.scope_goal),
            "scope_items": len(d.scope_items),
            "deliverables": len(d.deliverables),
            "milestones": len(d.milestones),
            "timeline_entries": len(d.timeline),
            "responsibilities": len(d.responsibilities),
            "components": {k: round(v, 4) for k, (_, v) in parts.items()},
        },
    )


def _scope_detail(parts: dict[str, tuple[float, float]]) -> str:
    missing = [key for key, (value, _) in parts.items() if value <= 0]
    if not missing:
        return "Goal, scope, deliverables, milestones, timeline and responsibilities are all documented."
    labels = {
        "goal": "a project goal",
        "scope": "scope items",
        "deliverables": "deliverables",
        "milestones": "milestones",
        "timeline": "a timeline",
        "responsibilities": "responsibilities",
    }
    return "Scope analysis is missing " + ", ".join(labels.get(m, m) for m in missing) + "."


def score_timeline_risk(d: HealthInputs, now: datetime | None = None) -> dict:
    """Required. Higher score = lower timeline risk."""
    now = now or datetime.now(timezone.utc)
    schedule = SCHEDULE_STATUS_SCORE.get((d.schedule_status or "").upper(), 0.0)
    schedule_supported = bool((d.schedule_status or "").strip())
    if not schedule_supported:
        schedule_factor = 0.0

    plan = 0.0
    if d.start_date and d.end_date:
        plan = 1.0 if d.end_date > d.start_date else 0.5

    completion_rate = (d.tasks_completed / d.tasks_total) if d.tasks_total else 0.0
    pace = _clamp(completion_rate / 0.6) if d.tasks_total else 0.0

    overdue_penalty = min(OVERDUE_PENALTY_CAP / 100.0 * 5, d.tasks_overdue * OVERDUE_PENALTY_PER_TASK / 100.0)
    overdue = max(0.0, 1.0 - overdue_penalty)
    if d.tasks_total == 0:
        overdue = 0.0  # no tasks => no evidence of schedule control

    severe_blockers = sum(1 for b in d.open_blockers if b["severity"].lower() in ("critical", "high"))
    blocker_impact = max(0.0, 1.0 - 0.15 * severe_blockers)

    score = 100.0 * (
        0.30 * schedule
        + 0.20 * plan
        + 0.20 * pace
        + 0.15 * overdue
        + 0.15 * blocker_impact
    )

    if not schedule_supported:
        detail = "No delivery forecast is available, so schedule status contributes nothing to this score."
    elif severe_blockers or d.tasks_overdue:
        detail = (
            f"Schedule is '{d.schedule_status}'"
            + (f" with {d.tasks_overdue} overdue task(s)" if d.tasks_overdue else "")
            + (f" and {severe_blockers} high/critical blocker(s)" if severe_blockers else "")
            + "."
        )
    else:
        detail = f"Schedule is '{d.schedule_status}' with no overdue tasks and no high-severity blockers."

    return _dim(
        "timeline_risk",
        score,
        supported=True,
        detail=detail,
        facts={
            "schedule_status": d.schedule_status or NOT_SPECIFIED,
            "schedule_supported": schedule_supported,
            "start_date": d.start_date.isoformat() if d.start_date else None,
            "expected_end_date": d.end_date.isoformat() if d.end_date else None,
            "plan_documented": plan > 0,
            "task_completion_rate": round(completion_rate, 4),
            "overdue_tasks": d.tasks_overdue,
            "high_severity_blockers": severe_blockers,
            "expected_delivery": d.forecast_expected_delivery or NOT_SPECIFIED,
        },
    )


def score_blocker_count(d: HealthInputs) -> dict:
    """Required. Weighted open blockers against a budget of 5."""
    weighted = d.blockers_weighted
    score = 100.0 * max(0.0, 1.0 - weighted / 5.0)
    if d.blockers_open == 0:
        detail = (
            "No open blockers."
            if d.blockers_total
            else "No blockers have been recorded for this project."
        )
    else:
        detail = f"{d.blockers_open} open blocker(s) totalling {weighted:.1f} weighted points."
    return _dim(
        "blocker_count",
        score,
        supported=True,
        detail=detail,
        facts={
            "open_blockers": d.blockers_open,
            "total_blockers": d.blockers_total,
            "weighted": round(weighted, 2),
            "budget": 5.0,
            "by_severity": _count_by(d.open_blockers, "severity"),
        },
    )


def score_risk_exposure(d: HealthInputs) -> dict:
    """Optional. Total open risk score against a budget of 60."""
    if d.risks_total == 0:
        return _dim(
            "risk_exposure",
            0.0,
            supported=False,
            detail="No risk records exist for this project, so risk exposure is not scored.",
            facts={"risks_total": 0},
        )
    score = 100.0 * max(0.0, 1.0 - d.risk_score_total / 60.0)
    return _dim(
        "risk_exposure",
        score,
        supported=True,
        detail=(
            f"{d.risks_open} open of {d.risks_total} risk(s), "
            f"{d.risk_score_total} total risk points."
        ),
        facts={
            "risks_total": d.risks_total,
            "risks_open": d.risks_open,
            "risks_mitigated": d.risks_mitigated,
            "total_risk_points": d.risk_score_total,
            "budget": 60,
            "by_severity": dict(d.risks_by_severity),
        },
    )


def score_deliverable_progress(d: HealthInputs) -> dict:
    """Optional. Task completion plus how well deliverables are documented."""
    if d.tasks_total == 0:
        return _dim(
            "deliverable_progress",
            0.0,
            supported=False,
            detail="No tasks exist for this project, so deliverable progress is not scored.",
            facts={"tasks_total": 0},
        )
    completion = d.tasks_completed / d.tasks_total
    documented = _saturate(d.documented_deliverables, 5)
    blocked_ratio = d.tasks_blocked / d.tasks_total
    task_health = max(0.0, 1.0 - 0.2 * blocked_ratio / 0.2) if blocked_ratio else 1.0
    score = 100.0 * (0.6 * completion + 0.2 * documented + 0.2 * task_health)
    return _dim(
        "deliverable_progress",
        score,
        supported=True,
        detail=(
            f"{d.tasks_completed}/{d.tasks_total} tasks complete "
            f"({round(completion * 100, 1)}%), {d.documented_deliverables} deliverable(s) documented."
        ),
        facts={
            "tasks_total": d.tasks_total,
            "tasks_completed": d.tasks_completed,
            "tasks_blocked": d.tasks_blocked,
            "completion_rate": round(completion, 4),
            "documented_deliverables": d.documented_deliverables,
        },
    )


def score_documentation_completeness(d: HealthInputs) -> dict:
    """Optional. Processed documents, indexed chunks and format coverage."""
    if d.documents_total == 0:
        return _dim(
            "documentation_completeness",
            0.0,
            supported=False,
            detail="No documents have been uploaded, so documentation completeness is not scored.",
            facts={"documents_total": 0},
        )
    processed = d.documents_processed / d.documents_total
    vectored = d.documents_with_chunks / d.documents_total
    coverage = min(1.0, len(d.document_types) / 4.0)
    score = 100.0 * (0.5 * processed + 0.3 * vectored + 0.2 * coverage)
    return _dim(
        "documentation_completeness",
        score,
        supported=True,
        detail=(
            f"{d.documents_processed}/{d.documents_total} documents processed, "
            f"{d.total_chunks} indexed chunks, {len(d.document_types)} of 4 supported formats present."
        ),
        facts={
            "documents_total": d.documents_total,
            "documents_processed": d.documents_processed,
            "documents_with_chunks": d.documents_with_chunks,
            "total_chunks": d.total_chunks,
            "formats": sorted(d.document_types),
        },
    )


def _count_by(rows: list[dict], key: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for row in rows:
        out[row.get(key, "Unknown")] = out.get(row.get(key, "Unknown"), 0) + 1
    return out


def _dim(key: str, score: float, *, supported: bool, detail: str, facts: dict) -> dict:
    weight = next((w for k, _, w, _ in DIMENSIONS if k == key), 1.0)
    return {
        "key": key,
        "label": dimension_label(key),
        "score": round(max(0.0, min(100.0, score)), 1),
        "weight": weight,
        "supported": supported,
        "detail": detail,
        "facts": facts,
    }


# --------------------------------------------------------------------------- #
# Report assembly
# --------------------------------------------------------------------------- #


def compute_dimensions(d: HealthInputs, now: datetime | None = None) -> list[dict]:
    return [
        score_scope_clarity(d),
        score_timeline_risk(d, now=now),
        score_blocker_count(d),
        score_risk_exposure(d),
        score_deliverable_progress(d),
        score_documentation_completeness(d),
    ]


def overall_from_dimensions(dimensions: list[dict]) -> tuple[float, list[str]]:
    """The published weighted average. Unsupported dimensions are excluded."""
    usable = [d for d in dimensions if d["supported"]]
    skipped = [d["key"] for d in dimensions if not d["supported"]]
    if not usable:
        return 0.0, skipped
    total_weight = sum(d["weight"] for d in usable)
    if total_weight <= 0:
        return 0.0, skipped
    weighted = sum(d["score"] * d["weight"] for d in usable)
    return round(max(0.0, min(100.0, weighted / total_weight)), 1), skipped


def data_completeness(d: HealthInputs) -> int:
    """0-100: how much of the expected project record actually exists.

    This is reported *next to* the health score and never folded into it, so a
    thinly-populated project cannot be mistaken for a healthy one.
    """
    checks = [
        bool(d.project_objective),
        d.documents_total > 0,
        d.tasks_total > 0,
        d.risks_total > 0,
        d.blockers_total > 0,
        bool(d.start_date),
        bool(d.end_date),
        len(d.deliverables) > 0,
        len(d.milestones) > 0,
        bool(d.schedule_status),
    ]
    return round(100.0 * sum(1 for c in checks if c) / len(checks))


def _positives(d: HealthInputs, dims: dict[str, dict]) -> list[dict]:
    out: list[dict] = []

    def add(kind: str, text: str, evidence: str) -> None:
        out.append({"type": kind, "text": text, "evidence": evidence})

    if d.blockers_open == 0:
        add("blockers", "No open blockers are recorded.", f"blockers: 0 of {d.blockers_total}")
    if d.tasks_total and d.tasks_completed == d.tasks_total:
        add(
            "tasks",
            f"All {d.tasks_total} tasks are complete.",
            f"tasks: {d.tasks_completed}/{d.tasks_total} completed",
        )
    elif d.tasks_total:
        rate = round(100.0 * d.tasks_completed / d.tasks_total, 1)
        if rate >= 50:
            add("tasks", f"Task completion is at {rate}%.", f"tasks: {d.tasks_completed}/{d.tasks_total}")
    if d.risks_mitigated:
        add(
            "risks",
            f"{d.risks_mitigated} risk(s) have been mitigated or closed.",
            f"risks: {d.risks_mitigated} of {d.risks_total}",
        )
    if d.documents_processed == d.documents_total and d.documents_total:
        add(
            "documents",
            f"All {d.documents_total} uploaded documents are processed and indexed ({d.total_chunks} chunks).",
            f"documents: {d.documents_processed}/{d.documents_total} processed",
        )
    if (d.schedule_status or "").upper() == "ON TRACK":
        add("schedule", "The delivery forecast reports the schedule as On Track.", "schedule_status: On Track")
    if d.scope_goal and len(d.deliverables) >= 3:
        add(
            "scope",
            f"A project goal and {len(d.deliverables)} deliverables are documented.",
            f"scope: goal + {len(d.deliverables)} deliverables",
        )
    for key in ("scope_clarity", "deliverable_progress", "documentation_completeness"):
        dim = dims.get(key)
        if dim and dim["supported"] and dim["score"] >= 75:
            add("dimension", f"{dim['label']} scores {dim['score']}/100.", dim["detail"])
    return out


def _negatives(d: HealthInputs, dims: dict[str, dict]) -> list[dict]:
    out: list[dict] = []

    def add(kind: str, text: str, evidence: str, severity: str = "medium") -> None:
        out.append({"type": kind, "text": text, "evidence": evidence, "severity": severity})

    if d.blockers_open:
        worst = ", ".join(f"{b['title']} ({b['severity']})" for b in d.open_blockers[:3])
        add(
            "blockers",
            f"{d.blockers_open} open blocker(s) are affecting delivery: {worst}.",
            f"blockers: {d.blockers_open} open",
            "high" if any(b["severity"].lower() in ("critical", "high") for b in d.open_blockers) else "medium",
        )
    for risk in d.major_risks[:5]:
        add(
            "risks",
            f"{risk['title']} — {risk['severity']} risk scoring {risk['score']}/25.",
            risk["evidence"] or f"risk record #{risk['id']}",
            "high" if risk["score"] >= 20 else "medium",
        )
    if d.tasks_overdue:
        add(
            "tasks",
            f"{d.tasks_overdue} task(s) are past their due date and still open.",
            f"tasks: {d.tasks_overdue} overdue of {d.tasks_total}",
            "high",
        )
    if d.tasks_blocked:
        add("tasks", f"{d.tasks_blocked} task(s) are marked Blocked.", f"tasks: {d.tasks_blocked} blocked", "medium")
    if (d.schedule_status or "") and (d.schedule_status or "").upper() != "ON TRACK":
        add(
            "schedule",
            f"The delivery forecast reports the schedule as '{d.schedule_status}'.",
            f"schedule_status: {d.schedule_status}",
            "high" if (d.schedule_status or "").upper() in ("AT RISK", "SIGNIFICANT RISK") else "medium",
        )
    elif not d.schedule_status:
        add("schedule", "No delivery forecast has been generated for this project.", "schedule_status: none", "medium")
    for key in ("scope_clarity", "timeline_risk", "blocker_count", "risk_exposure",
                "deliverable_progress", "documentation_completeness"):
        dim = dims.get(key)
        if dim and dim["supported"] and dim["score"] < 50:
            add("dimension", f"{dim['label']} scores {dim['score']}/100.", dim["detail"], "high")
    for key, dim in dims.items():
        if not dim["supported"]:
            add("coverage", f"{dim['label']} could not be scored — {dim['detail']}", "not enough data", "low")
    return out


def _recommendations(d: HealthInputs, dims: dict[str, dict]) -> list[dict]:
    """Each recommendation names the real record that motivates it."""
    out: list[dict] = []
    order = {"high": 0, "medium": 1, "low": 2}
    for item in _negatives(d, dims):
        if item["type"] == "blockers":
            text = f"Clear the {d.blockers_open} open blocker(s); each is a direct cause of schedule pressure."
        elif item["type"] == "risks":
            text = f"Agree a mitigation plan for {item['text'].split(' — ')[0]} and record it on the risk."
        elif item["type"] == "tasks" and "overdue" in item["text"]:
            text = "Re-plan or close the overdue tasks; they are already reducing the timeline score."
        elif item["type"] == "tasks":
            text = "Resolve the Blocked tasks; they hold back deliverable progress."
        elif item["type"] == "schedule":
            text = "Run the delivery forecast so the timeline score reflects the documented schedule."
        elif item["type"] == "dimension":
            text = f"Address {item['text'].split(' scores')[0].lower()} — it is one of the weakest scored dimensions."
        else:
            text = "Upload or analyse the missing project information so this dimension can be scored."
        out.append(
            {
                "priority": item.get("severity", "medium").title(),
                "text": text,
                "evidence": item["evidence"],
                "based_on": item["type"],
            }
        )
    out.sort(key=lambda r: order.get(r["priority"].lower(), 3))
    return out


def score_project(db: Session, project: Project, now: datetime | None = None) -> dict:
    """Deterministic health evaluation. No model, no randomness, no clock
    dependence in the score itself (``now`` only affects overdue-task counting
    and is injectable for reproducible tests)."""
    now = now or datetime.now(timezone.utc)
    data = collect_inputs(db, project, now=now)
    dims = compute_dimensions(data, now=now)
    overall, skipped = overall_from_dimensions(dims)
    by_key = {d["key"]: d for d in dims}
    return {
        "project_id": project.id,
        "project_name": project.name,
        "formula_version": FORMULA_VERSION,
        "overall_score": overall,
        "status": status_for(overall),
        "dimensions": dims,
        "skipped_dimensions": skipped,
        "data_completeness": data_completeness(data),
        "positives": _positives(data, by_key),
        "negatives": _negatives(data, by_key),
        "recommendations": _recommendations(data, by_key),
        "inputs": data.as_dict(),
        "narrative": "",
        "narrative_provider": "",
        "narrative_model": "",
        "has_analysis": bool(data.last_analysis_at),
        "last_analysis_at": data.last_analysis_at,
    }


__all__ = [
    "HealthInputs",
    "collect_inputs",
    "score_scope_clarity",
    "score_timeline_risk",
    "score_blocker_count",
    "score_risk_exposure",
    "score_deliverable_progress",
    "score_documentation_completeness",
    "compute_dimensions",
    "overall_from_dimensions",
    "data_completeness",
    "score_project",
    "FORMULA_TEXT",
    "FORMULA_VERSION",
    "MIN_DOCUMENTED_DELIVERABLES",
    "OPTIONAL_DIMENSIONS",
]
