"""Automatic post-upload project analysis.

The product rule this module implements:

    upload a document -> the whole project intelligence pipeline runs by itself

Nothing here is a new agent and nothing here re-implements analysis. Every stage
delegates to code that already existed:

===========================  ============================================
stage                        delegated to
===========================  ============================================
scope / deliverables         ``agents.scope.ScopeAgent``            (via orchestrator)
risks                        ``agents.risk.RiskAgent``              (via orchestrator)
blockers                     ``agents.blocker.BlockerAgent``        (via orchestrator)
action items                 ``agents.action.ActionItemAgent``      (via orchestrator)
delivery forecast            ``agents.forecast.ForecastAgent``      (via orchestrator)
health score                 ``milestone3.health.scorer``  -> deterministic formula
generated documents          ``milestone3.documentation.generator.generate_all``
assistant context            already written by ingestion (chunks + vectors)
===========================  ============================================

Design constraints honoured here:

* **Grounded.** No stage fabricates a value. The agents read from the uploaded
  documents, and the deterministic health formula and the documentation
  validators refuse to invent owners or dates.
* **Idempotent.** Re-running replaces rather than duplicates: the orchestrator
  clears prior ``ai_detected`` rows first, and ``generate_document`` upserts on
  ``(project_id, doc_type)``.
* **Never fatal.** A failed optional AI stage is recorded and the pipeline
  continues, so one provider outage cannot break the project.
* **Assistant stays user-driven.** This module never creates a conversation, a
  question or a message. It only makes sure the RAG index the assistant reads
  is warm, which ingestion already did.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models import Blocker, Project, ProjectDocument, ProjectInsight, Risk, Task
from app.models_m3 import GeneratedDocument, HealthSnapshot, ProjectPipelineRun
from app.services.milestone3.common import NOT_SPECIFIED
from app.services.milestone3.documentation.dates import (
    SOURCE_ADMIN,
    SOURCE_DOCUMENT,
    SOURCE_NONE,
)

logger = logging.getLogger("pipeline")

#: Ordered stage list. The keys are stable identifiers; the labels are exactly
#: the strings the UI renders, so the two can never drift.
STAGES: tuple[tuple[str, str], ...] = (
    ("document_processed", "Document processed"),
    ("information_extracted", "Project information extracted"),
    ("tasks_analyzed", "Tasks analyzed"),
    ("risks_analyzed", "Risks analyzed"),
    ("blockers_analyzed", "Blockers analyzed"),
    ("forecast_calculated", "Forecast calculated"),
    ("health_calculated", "Health score calculated"),
    ("insights_prepared", "AI insights prepared"),
    ("documents_ready", "Generated documents ready"),
)

STAGE_KEYS = tuple(k for k, _ in STAGES)

STATUS_RUNNING = "Running"
STATUS_COMPLETED = "Completed"
STATUS_PARTIAL = "CompletedWithWarnings"
STATUS_FAILED = "Failed"

#: Due-date evidence is quoted, not stored wholesale — this keeps the generated
#: document payloads small. A clipped quote is always marked with an ellipsis.
EVIDENCE_LIMIT = 500


# ---------------------------------------------------------------------------
# project due date — document first, project record second, never invented
# ---------------------------------------------------------------------------


def _date_only(value) -> str:
    """Render a stored due date as a plain calendar date.

    ``projects.expected_end_date`` is a DateTime column, so reading it straight
    off the model yields ``2026-01-15T00:00:00``. A due date must never be shown
    (or written into a generated document) with a time component the user never
    entered, so normalise to the date part here.
    """
    if not value:
        return ""
    if hasattr(value, "date") and not isinstance(value, str):
        # datetime.datetime -> drop the time part; datetime.date passes through.
        try:
            return value.date().isoformat()
        except AttributeError:
            return ""
    text = str(value).strip()
    if not text:
        return ""
    # Already an ISO date/datetime string, e.g. from an API payload.
    return text[:10] if len(text) > 10 and text[4:5] == "-" and text[7:8] == "-" else text


def resolve_project_due_date(db: Session, project: Project) -> dict:
    """Resolve the project's due date using the document-first precedence rule.

    Priority 1  an explicit due date found in an uploaded document
    Priority 2  ``projects.expected_end_date`` entered on the project form
    Priority 3  unknown / not specified

    The admin value is never destroyed: both inputs are returned so the
    precedence stays auditable, and ``conflict`` says whether the document
    actually disagreed with it.

    Only *processed* documents are searched, so a half-written upload can never
    supply a date.
    """
    from app.services.milestone3.documentation.extraction import find_due_date

    admin_value = project.expected_end_date
    admin_text = _date_only(admin_value)

    hit = None
    docs = (
        db.query(ProjectDocument)
        .filter(
            ProjectDocument.project_id == project.id,
            ProjectDocument.status == "Processed",
        )
        .order_by(ProjectDocument.id.desc())
        .all()
    )
    for doc in docs:
        text = doc.extracted_text or ""
        if not text.strip():
            continue
        found = find_due_date(text)
        if found is not None:
            hit = found
            hit.document = doc.original_name
            break

    document_date = hit.value.isoformat() if hit else ""
    effective = document_date or admin_text or ""
    source = SOURCE_DOCUMENT if document_date else (SOURCE_ADMIN if admin_text else SOURCE_NONE)

    evidence = (hit.evidence if hit and hit.evidence else "") or ""
    if len(evidence) > EVIDENCE_LIMIT:
        # Mark the clip so a reader never mistakes a truncated quote for the
        # whole document.
        evidence = evidence[:EVIDENCE_LIMIT].rstrip() + " ..."

    return {
        "effective_date": effective or NOT_SPECIFIED,
        "admin_date": admin_text or NOT_SPECIFIED,
        "document_date": document_date or NOT_SPECIFIED,
        "source": source,
        "evidence": evidence or NOT_SPECIFIED,
        "document": (hit.document if hit else "") or "",
        "conflict": bool(document_date and admin_text and document_date != admin_text),
    }


# ---------------------------------------------------------------------------
# live aggregates — the status the UI polls
# ---------------------------------------------------------------------------


def live_counts(db: Session, project_id: int) -> dict:
    """Real, current row counts for one project. No score is invented here."""
    open_status = ("Open", "In Progress")
    return {
        "documents": db.query(ProjectDocument).filter(ProjectDocument.project_id == project_id).count(),
        "documents_processed": db.query(ProjectDocument)
        .filter(ProjectDocument.project_id == project_id, ProjectDocument.status == "Processed")
        .count(),
        "documents_processing": db.query(ProjectDocument)
        .filter(ProjectDocument.project_id == project_id, ProjectDocument.status.in_(["Uploaded", "Processing"]))
        .count(),
        "documents_failed": db.query(ProjectDocument)
        .filter(ProjectDocument.project_id == project_id, ProjectDocument.status == "Failed")
        .count(),
        "scope_items": db.query(ProjectInsight)
        .filter(ProjectInsight.project_id == project_id, ProjectInsight.agent == "scope")
        .count(),
        "deliverables": db.query(ProjectInsight)
        .filter(ProjectInsight.project_id == project_id, ProjectInsight.agent == "scope", ProjectInsight.category == "deliverable")
        .count(),
        "tasks": db.query(Task).filter(Task.project_id == project_id).count(),
        "ai_action_items": db.query(Task)
        .filter(Task.project_id == project_id, Task.ai_generated == True)  # noqa: E712
        .count(),
        "risks": db.query(Risk).filter(Risk.project_id == project_id).count(),
        "blockers": db.query(Blocker).filter(Blocker.project_id == project_id).count(),
        "open_risks": db.query(Risk)
        .filter(Risk.project_id == project_id, Risk.status.in_(open_status))
        .count(),
        "open_blockers": db.query(Blocker)
        .filter(Blocker.project_id == project_id, Blocker.status.in_(open_status))
        .count(),
        "insights": db.query(ProjectInsight).filter(ProjectInsight.project_id == project_id).count(),
        "forecast": db.query(ProjectInsight)
        .filter(ProjectInsight.project_id == project_id, ProjectInsight.agent == "forecast")
        .count(),
        "generated_documents": db.query(GeneratedDocument)
        .filter(GeneratedDocument.project_id == project_id)
        .count(),
        "health_snapshots": db.query(HealthSnapshot).filter(HealthSnapshot.project_id == project_id).count(),
    }


def latest_health(db: Session, project_id: int) -> dict | None:
    snap = (
        db.query(HealthSnapshot)
        .filter(HealthSnapshot.project_id == project_id)
        .order_by(HealthSnapshot.id.desc())
        .first()
    )
    if not snap:
        return None
    return {
        "overall_score": snap.overall_score,
        "status": snap.status,
        "formula_version": snap.formula_version,
        "data_completeness": snap.data_completeness,
        "created_at": snap.created_at,
    }


# ---------------------------------------------------------------------------
# the pipeline
# ---------------------------------------------------------------------------


def _blank_stages() -> list[dict]:
    return [{"key": k, "label": label, "status": "pending", "detail": ""} for k, label in STAGES]


def _mark(stages: list[dict], key: str, status: str, detail: str = "") -> None:
    for stage in stages:
        if stage["key"] == key:
            stage["status"] = status
            stage["detail"] = detail
            return


def _fail(stages: list[dict], key: str, detail: str) -> None:
    _mark(stages, key, "failed", detail)


def _skip(stages: list[dict], key: str, detail: str) -> None:
    _mark(stages, key, "skipped", detail)


def latest_run(db: Session, project_id: int) -> ProjectPipelineRun | None:
    return (
        db.query(ProjectPipelineRun)
        .filter(ProjectPipelineRun.project_id == project_id)
        .order_by(ProjectPipelineRun.id.desc())
        .first()
    )


def run_to_dict(run: ProjectPipelineRun | None, db: Session, project_id: int) -> dict:
    counts = live_counts(db, project_id)
    project = db.query(Project).filter(Project.id == project_id).first()
    due = resolve_project_due_date(db, project) if project else {}

    stages = list(run.stages) if run and run.stages else _blank_stages()
    if not run:
        # Nothing has ever run. Report the stages honestly from live data.
        if counts["documents_processed"] == 0:
            _skip(stages, "document_processed", "No processed document yet.")

    return {
        "project_id": project_id,
        "running": bool(run and run.status == STATUS_RUNNING),
        "status": run.status if run else "Not started",
        "trigger": run.trigger if run else "",
        "current_stage": run.current_stage if run else "",
        "stages": stages,
        "counts": counts,
        "error": (run.error if run else "") or "",
        "started_at": run.created_at if run else None,
        "finished_at": run.finished_at if run else None,
        "due_date": due,
        "health": latest_health(db, project_id),
        "assistant_ready": counts["documents_processed"] > 0,
    }


def run_project_pipeline(
    db: Session,
    project_id: int,
    *,
    document_id: int | None = None,
    created_by: int | None = None,
    trigger: str = "document_upload",
    audit_request=None,
) -> dict:
    """Run the whole intelligence pipeline for one project.

    Extends the existing orchestrator; does not replace it and adds no new
    agent. Returns the pipeline status dict so callers and tests can assert on
    it without a second query.
    """
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise ValueError(f"Project {project_id} not found.")

    run = ProjectPipelineRun(
        project_id=project_id,
        document_id=document_id,
        trigger=trigger,
        status=STATUS_RUNNING,
        current_stage=STAGES[0][0],
        stages=_blank_stages(),
        counts={},
        created_by=created_by,
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    stages: list[dict] = list(run.stages)
    counts: dict = {}
    warnings: list[str] = []

    def flush(stage_key: str) -> None:
        run.stages = stages
        run.current_stage = stage_key
        db.commit()

    # -- stage 1: document ingestion ----------------------------------------
    processed_docs = (
        db.query(ProjectDocument)
        .filter(ProjectDocument.project_id == project_id, ProjectDocument.status == "Processed")
        .count()
    )
    if document_id:
        doc = db.query(ProjectDocument).filter(ProjectDocument.id == document_id).first()
        if doc and doc.status == "Processed":
            _mark(stages, "document_processed", "done", f"{doc.original_name} · {doc.chunk_count or 0} chunks")
        else:
            status = doc.status if doc else "missing"
            _skip(stages, "document_processed", f"Document {document_id} is {status}.")
    elif processed_docs:
        _mark(stages, "document_processed", "done", f"{processed_docs} document(s) processed.")
    else:
        _skip(stages, "document_processed", "No processed document to analyze.")
    flush("information_extracted")

    if processed_docs == 0:
        # Nothing to ground an analysis in. Never fabricate: mark the rest as
        # skipped and finish cleanly.
        for key in STAGE_KEYS[1:]:
            _skip(stages, key, "No processed document available.")
        run.stages = stages
        run.status = STATUS_COMPLETED
        run.counts = {"documents_processed": 0}
        run.finished_at = datetime.now(timezone.utc)
        run.current_stage = ""
        db.commit()
        return run_to_dict(run, db, project_id)

    # -- stages 2-6: the existing multi-agent orchestrator -------------------
    from app.services.agents.orchestrator import analyze_project

    try:
        analysis = analyze_project(db, project_id, created_by=created_by, audit_request=audit_request)
    except Exception as exc:  # noqa: BLE001 - a failed run must not 500 the upload
        logger.exception("Pipeline agent run failed for project %s", project_id)
        db.rollback()
        warnings.append(f"Analysis agents failed: {exc}")
        for key in ("information_extracted", "tasks_analyzed", "risks_analyzed",
                    "blockers_analyzed", "forecast_calculated", "insights_prepared"):
            _fail(stages, key, str(exc)[:300])
        analysis = {"agents": {}}
    else:
        agents = analysis.get("agents", {})
        mapping = {
            "information_extracted": "scope",
            "tasks_analyzed": "action",
            "risks_analyzed": "risk",
            "blockers_analyzed": "blocker",
            "forecast_calculated": "forecast",
        }
        for stage_key, agent_key in mapping.items():
            info = agents.get(agent_key)
            if not info:
                _fail(stages, stage_key, "Agent did not report a result.")
                warnings.append(f"{agent_key} agent did not report.")
                continue
            status = info.get("status")
            count = info.get("count", 0)
            if status == "Completed":
                _mark(stages, stage_key, "done", f"{count} record(s)")
            elif status == "Failed":
                reason = info.get("error") or "Agent failed."
                _fail(stages, stage_key, reason[:300])
                warnings.append(f"{agent_key}: {reason[:160]}")
            else:
                _fail(stages, stage_key, str(status))
        counts["agents"] = agents
    flush("health_calculated")

    # -- stage 7: deterministic health score --------------------------------
    health_report: dict | None = None
    try:
        from app.services.milestone3.health.service import get_health, record_audit

        # explain=False keeps the automatic run fast and provider-independent.
        # The numeric score is the deterministic formula either way, and the
        # Health tab still requests the narrative on demand.
        health_report = get_health(
            db, project, persist=True, explain=False, created_by=created_by
        )
        record_audit(
            db, project_id, created_by, "Automatic health score after document ingestion."
        )
        _mark(
            stages,
            "health_calculated",
            "done",
            f"{health_report['overall_score']}/100 · {health_report['status']}",
        )
        counts["health_score"] = health_report["overall_score"]
        counts["health_status"] = health_report["status"]
    except Exception as exc:  # noqa: BLE001
        logger.exception("Deterministic health scoring failed for project %s", project_id)
        db.rollback()
        _fail(stages, "health_calculated", str(exc)[:300])
        warnings.append(f"Health scoring failed: {exc}")
    flush("insights_prepared")

    # -- stage 8: insights readiness ----------------------------------------
    insight_count = db.query(ProjectInsight).filter(ProjectInsight.project_id == project_id).count()
    if insight_count:
        _mark(stages, "insights_prepared", "done", f"{insight_count} insight(s) available.")
        counts["insights"] = insight_count
    else:
        _skip(stages, "insights_prepared", "No insights were produced from the documents.")
    flush("documents_ready")

    # -- stage 9: generated documents ---------------------------------------
    try:
        from app.services.milestone3.documentation.generator import generate_all

        docs = generate_all(db, project, created_by=created_by, audit_request=audit_request)
        counts["generated_documents"] = len(docs)
        if docs:
            labels = ", ".join(d.doc_type for d in docs)
            _mark(stages, "documents_ready", "done", f"{len(docs)} document(s): {labels}")
            missing = [d.doc_type for d in docs if d.insufficient_evidence]
            if missing:
                warnings.append(
                    "Insufficient document evidence for: " + ", ".join(missing)
                )
        else:
            _fail(stages, "documents_ready", "No generated document could be produced.")
            warnings.append("Generated documents could not be produced.")
    except Exception as exc:  # noqa: BLE001
        logger.exception("Generated documentation failed for project %s", project_id)
        db.rollback()
        _fail(stages, "documents_ready", str(exc)[:300])
        warnings.append(f"Generated documents failed: {exc}")

    # -- finish --------------------------------------------------------------
    failed_stages = [s for s in stages if s["status"] == "failed"]
    run.status = STATUS_FAILED if len(failed_stages) == len(stages) else (
        STATUS_PARTIAL if failed_stages or warnings else STATUS_COMPLETED
    )
    run.counts = counts
    run.stages = stages
    run.current_stage = ""
    run.error = "; ".join(warnings)[:2000] if warnings else None
    run.due_date = resolve_project_due_date(db, project)
    run.finished_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(run)

    log_pipeline_event(db, project_id, created_by, run)

    return run_to_dict(run, db, project_id)


def log_pipeline_event(db: Session, project_id: int, created_by: int | None, run: ProjectPipelineRun) -> None:
    from app.services.audit import log_event

    done = sum(1 for s in (run.stages or []) if s["status"] == "done")
    log_event(
        db,
        user_id=created_by,
        user_email="",
        action="project_analysis_automatic",
        resource_type="project",
        resource_id=str(project_id),
        project_id=project_id,
        detail=f"{run.status}: {done}/{len(run.stages or [])} stages.",
        request=None,
    )


def pipeline_status(db: Session, project_id: int) -> dict:
    """Poll-friendly snapshot for the frontend progress panel."""
    return run_to_dict(latest_run(db, project_id), db, project_id)


__all__ = [
    "STAGES",
    "STAGE_KEYS",
    "STATUS_RUNNING",
    "STATUS_COMPLETED",
    "STATUS_PARTIAL",
    "STATUS_FAILED",
    "run_project_pipeline",
    "pipeline_status",
    "live_counts",
    "latest_run",
    "run_to_dict",
    "latest_health",
    "resolve_project_due_date",
]