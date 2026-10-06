from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models import AiRun, Blocker, Project, ProjectDocument, ProjectInsight, Risk, Task
from app.services.agents.action import ActionItemAgent
from app.services.agents.blocker import BlockerAgent
from app.services.agents.forecast import ForecastAgent
from app.services.agents.risk import RiskAgent
from app.services.agents.scope import ScopeAgent
from app.services.audit import log_event
from app.services.rag import project_document_summary

logger = logging.getLogger("orchestrator")

AGENT_ORDER = ["scope", "risk", "forecast", "blocker", "action"]


def _source_refs(chunks: list[dict]) -> list[dict]:
    from app.services.ai.schemas import SourceRef

    seen = set()
    out = []
    for c in chunks:
        doc = c.get("original_name") or ""
        page = _int_or_none(c.get("page"))
        key = (doc, page, c.get("section", ""))
        if key in seen:
            continue
        seen.add(key)
        out.append(
            SourceRef(
                document=doc,
                page=page,
                section=c.get("section") or None,
                quote=c.get("text", "")[:300],
            ).model_dump()
        )
    return out


def _int_or_none(v) -> int | None:
    try:
        return int(v) if v not in ("", None) else None
    except (TypeError, ValueError):
        return None


def _parse_date(value: str) -> datetime | None:
    if not value:
        return None
    value = value.strip()
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%m/%d/%Y", "%d %B %Y", "%B %d %Y", "%Y/%m/%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def clear_agent_outputs(db: Session, project_id: int, agents: list[str]) -> None:
    """Remove prior agent results for a fresh, non-duplicating analysis run."""
    if "risk" in agents:
        db.query(Risk).filter(Risk.project_id == project_id, Risk.source_type == "ai_detected").delete()
    if "blocker" in agents:
        db.query(Blocker).filter(Blocker.project_id == project_id, Blocker.source_type == "ai_detected").delete()
    if "action" in agents:
        db.query(Task).filter(Task.project_id == project_id, Task.ai_generated == True).delete()  # noqa: E712
    db.query(ProjectInsight).filter(ProjectInsight.project_id == project_id, ProjectInsight.agent.in_(agents)).delete()
    db.commit()


def _doc_id_map(db: Session, project_id: int) -> dict[str, int]:
    docs = db.query(ProjectDocument).filter(ProjectDocument.project_id == project_id).all()
    lower: dict[str, int] = {}
    for d in docs:
        lower[d.original_name.strip().lower()] = d.id
    return lower


def _find_doc_id(map_: dict[str, int], name: str) -> int | None:
    if not name:
        return None
    return map_.get(name.strip().lower())


def persist_scope(db: Session, project_id: int, run_id: int, result, chunks: list[dict]) -> int:
    refs = _source_refs(chunks)
    if not result:
        return 0
    count = 0

    def add(category: str, title: str, summary: str = "", payload: dict | None = None) -> None:
        nonlocal count
        db.add(
            ProjectInsight(
                project_id=project_id, agent="scope", category=category,
                title=(title or "")[:255], summary=summary or "", payload=payload or {},
                evidence=refs, ai_run_id=run_id,
            )
        )
        count += 1

    if result.project_goal:
        add("project_goal", "Project Goal", result.project_goal)
    for item in result.scope:
        add("scope", item)
    for item in result.out_of_scope:
        add("out_of_scope", item, "Out of scope")
    for item in result.deliverables:
        add("deliverable", item)
    for item in result.milestones:
        add("milestone", item.name, item.date or "", {"date": item.date})
    for item in result.timeline:
        add("timeline", item)
    for item in result.responsibilities:
        add("responsibility", item)
    for item in result.technologies:
        add("technology", item)
    for item in result.requirements:
        add("requirement", item)
    return count


def persist_risks(db: Session, project_id: int, run_id: int, result, chunks: list[dict]) -> int:
    if not result or not result.risks:
        return 0
    doc_map = _doc_id_map(db, project_id)
    refs = _source_refs(chunks)
    count = 0
    for r in result.risks:
        source_id = _find_doc_id(doc_map, r.source_document)
        evidence = r.evidence or "Evidence not found in uploaded documents."
        risk = Risk(
            project_id=project_id,
            title=(r.title or "Risk")[:255],
            description=r.description or "",
            severity=r.severity,
            probability=r.probability,
            impact=r.impact,
            evidence=evidence,
            source_document_id=source_id,
            recommended_action=r.recommended_action or "",
            status="Open",
            source_type="ai_detected",
        )
        db.add(risk)
        db.flush()
        db.add(
            ProjectInsight(
                project_id=project_id, agent="risk", category=f"risk_{r.severity.lower()}",
                title=(r.title or "")[:255], summary=(r.description or "")[:2000],
                payload={"severity": r.severity, "probability": r.probability, "impact": r.impact, "recommended_action": r.recommended_action, "risk_id": risk.id},
                evidence=refs, ai_run_id=run_id,
            )
        )
        count += 1
    return count


def persist_blockers(db: Session, project_id: int, run_id: int, result, chunks: list[dict]) -> int:
    if not result or not result.blockers:
        return 0
    doc_map = _doc_id_map(db, project_id)
    refs = _source_refs(chunks)
    count = 0
    for b in result.blockers:
        source_id = _find_doc_id(doc_map, b.source_document)
        blocker = Blocker(
            project_id=project_id,
            title=(b.title or "Blocker")[:255],
            description=b.description or "",
            severity=b.severity,
            owner=b.owner or "",
            expected_resolution=b.recommended_action or "",
            status="Open",
            source_type="ai_detected",
            evidence=b.evidence or "",
            source_document_id=source_id,
        )
        db.add(blocker)
        db.flush()
        db.add(
            ProjectInsight(
                project_id=project_id, agent="blocker", category=f"blocker_{b.severity.lower()}",
                title=(b.title or "")[:255], summary=(b.description or "")[:2000],
                payload={"severity": b.severity, "owner": b.owner, "blocker_id": blocker.id, "recommended_action": b.recommended_action},
                evidence=refs, ai_run_id=run_id,
            )
        )
        count += 1
    return count


def _member_name_map(db: Session, project_id: int) -> dict[str, int]:
    from app.models import ProjectMember, User

    rows = (
        db.query(ProjectMember)
        .join(User, User.id == ProjectMember.user_id)
        .filter(ProjectMember.project_id == project_id)
        .all()
    )
    out: dict[str, int] = {}
    for row in rows:
        out[row.user.name.strip().lower()] = row.user.id
        out[row.user.email.strip().lower()] = row.user.id
    return out


def persist_actions(db: Session, project_id: int, run_id: int, result, chunks: list[dict], created_by: int | None) -> int:
    if not result or not result.action_items:
        return 0
    doc_map = _doc_id_map(db, project_id)
    name_map = _member_name_map(db, project_id)
    refs = _source_refs(chunks)
    count = 0
    for item in result.action_items:
        source_id = _find_doc_id(doc_map, (item.source_document or ""))
        assigned_to = name_map.get((item.assigned_person or "").strip().lower())
        desc = item.assigned_person or ""
        if assigned_to is None and desc:
            desc = f"Assigned to: {desc}"
        task = Task(
            project_id=project_id,
            title=(item.action or "Action item")[:255],
            description=desc or "",
            assigned_to=assigned_to,
            due_date=_parse_date(item.deadline),
            priority=item.priority,
            status="Pending",
            source_type="ai_generated",
            source_document_id=source_id,
            source_ref=_source_ref_text(item),
            ai_generated=True,
            created_by=created_by,
        )
        db.add(task)
        db.flush()
        db.add(
            ProjectInsight(
                project_id=project_id, agent="action", category="action",
                title=(item.action or "")[:255], summary=desc or "",
                payload={"assigned_person": item.assigned_person, "deadline": item.deadline, "priority": item.priority, "task_id": task.id},
                evidence=refs, ai_run_id=run_id,
            )
        )
        count += 1
    return count


def _source_ref_text(item) -> str:
    parts = []
    if item.source_document:
        parts.append(item.source_document)
    if item.source_page:
        parts.append(f"Page {item.source_page}")
    if item.source_section:
        parts.append(item.source_section)
    return " — ".join(parts)


def persist_forecast(db: Session, project_id: int, run_id: int, result, chunks: list[dict]) -> int:
    if not result:
        return 0
    db.add(
        ProjectInsight(
            project_id=project_id, agent="forecast", category="delivery_forecast",
            title="Delivery Forecast", summary=result.current_status or "",
            payload={
                "current_status": result.current_status,
                "schedule_status": result.schedule_status,
                "expected_delivery": result.expected_delivery,
                "risk_level": result.risk_level,
                "factors": [f.model_dump() for f in result.factors],
                "caveat": result.caveat,
            },
            evidence=_source_refs(chunks), ai_run_id=run_id,
        )
    )
    return 1


def persist_health_metrics(db: Session, project_id: int) -> dict:
    """Prepare the underlying data a future Health scoring module needs."""
    stats = project_document_summary(db, project_id)
    forecast = (
        db.query(ProjectInsight)
        .filter(ProjectInsight.project_id == project_id, ProjectInsight.agent == "forecast")
        .order_by(ProjectInsight.id.desc())
        .first()
    )
    schedule = (forecast.payload.get("schedule_status") if forecast else None) or "Unknown"
    metrics = {
        "task_completion_rate": stats["completion_rate"],
        "schedule_status": schedule,
        "documents": stats["documents"],
        "open_risks": stats["open_risks"],
        "open_blockers": stats["open_blockers"],
        "total_tasks": stats["total_tasks"],
        "edge": "health scoring service can compute a transparent score later",
    }
    db.add(
        ProjectInsight(
            project_id=project_id, agent="health", category="metrics",
            title="Health Metrics", summary="",
            payload=metrics, evidence=[],
        )
    )
    return metrics


def analyze_project(
    db: Session,
    project_id: int,
    *,
    created_by: int | None,
    audit_request=None,
    agents: list[str] | None = None,
) -> dict:
    """Run the full agent pipeline. Returns per-agent results with counts."""
    agents = agents or AGENT_ORDER
    clear_agent_outputs(db, project_id, agents)

    runner_map = {
        "scope": ScopeAgent(),
        "risk": RiskAgent(),
        "forecast": ForecastAgent(),
        "blocker": BlockerAgent(),
        "action": ActionItemAgent(),
    }

    summary: dict[str, dict] = {}
    for key in agents:
        agent = runner_map.get(key)
        if not agent:
            continue
        run = AiRun(project_id=project_id, agent=key, status="Running")
        db.add(run)
        db.commit()
        db.refresh(run)

        started = datetime.now(timezone.utc)
        try:
            result = agent.run(db, project_id)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Agent %s failed for project %s", key, project_id)
            result = {"status": "error", "data": None, "sources": [], "error": str(exc)}

        ended = datetime.now(timezone.utc)
        run.provider = result.get("provider", "")
        run.model = result.get("model", "")
        run.source_document_ids = _source_doc_ids(result.get("sources", []))
        run.ended_at = ended
        run.result_metadata = {"count": 0}

        counts = {"scope": 0, "risk": 0, "forecast": 0, "blocker": 0, "action": 0}
        data = result.get("data")
        if result.get("status") == "no_documents":
            run.status = "Failed"
            run.error = "No processed documents to analyze."
        elif result.get("status") == "error":
            run.status = "Failed"
            run.error = (result.get("error") or "Agent execution error")[:2000]
        elif result.get("status") == "invalid_output":
            run.status = "Failed"
            run.error = "Agent returned invalid structured output."
        else:
            if key == "scope":
                counts["scope"] = persist_scope(db, project_id, run.id, data, result.get("sources", []))
            elif key == "risk":
                counts["risk"] = persist_risks(db, project_id, run.id, data, result.get("sources", []))
            elif key == "forecast":
                counts["forecast"] = persist_forecast(db, project_id, run.id, data, result.get("sources", []))
            elif key == "blocker":
                counts["blocker"] = persist_blockers(db, project_id, run.id, data, result.get("sources", []))
            elif key == "action":
                counts["action"] = persist_actions(db, project_id, run.id, data, result.get("sources", []), created_by)
            run.status = "Completed"

        db.commit()
        summary[key] = {"status": run.status, "count": counts.get(key, 0), "error": run.error}
        logger.info("Agent %s for project %s -> %s (%d)", key, project_id, run.status, counts.get(key, 0))

    metrics = persist_health_metrics(db, project_id)
    db.commit()

    log_event(
        db,
        user_id=created_by,
        user_email="",
        action="ai_analysis_generated",
        resource_type="project",
        resource_id=str(project_id),
        project_id=project_id,
        detail=f"Analysis for project {project_id} completed.",
        request=audit_request,
    )

    return {"project_id": project_id, "agents": summary, "health_metrics": metrics}


def _source_doc_ids(chunks: list[dict]) -> list[int]:
    ids: list[int] = []
    for c in chunks:
        try:
            ids.append(int(c.get("document_id", "")))
        except (TypeError, ValueError):
            continue
    return list(dict.fromkeys(ids))
