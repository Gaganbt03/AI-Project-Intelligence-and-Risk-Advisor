"""Health service — persistence, explanation and history.

The score itself lives in :mod:`app.services.milestone3.health.scorer` and is
pure. This module handles everything around it:

* caching a :class:`~app.models_m3.HealthSnapshot` per request,
* asking the LLM to *explain* a score that has already been computed,
* serving the latest snapshot and the score history.

The explanation prompt is given the finished numbers and is explicitly forbidden
from producing or changing a score. If the provider is unavailable the report is
still returned in full, just without prose.
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.models import Project
from app.models_m3 import HealthSnapshot
from app.services.ai.providers import AIProviderError, get_provider_manager
from app.services.audit import log_event
from app.services.milestone3.health.schemas import FORMULA_TEXT, FORMULA_VERSION
from app.services.milestone3.health.scorer import score_project

logger = logging.getLogger("m3.health.service")

EXPLANATION_SYSTEM = (
    "You are a project delivery analyst. You are given an ALREADY COMPUTED project health "
    "score with its dimension breakdown, positive factors, negative factors and "
    "recommendations.\n\n"
    "HARD RULES:\n"
    "- You must NOT output, restate as a new figure, or change any number. The numbers are final.\n"
    "- Explain only why the score came out as it did, using the supplied factors.\n"
    "- Every claim must trace back to a factor listed in the input.\n"
    "- Write 3-6 sentences of plain prose. No markdown headings, no lists, no preamble.\n"
    "- If the input is empty, say that there is not enough project data to explain a score."
)

MAX_NARRATIVE_CHARS = 1200


def _explanation_prompt(report: dict) -> str:
    dims = "\n".join(
        f"- {d['label']}: {d['score']}/100"
        + ("" if d["supported"] else "  (not scored - insufficient data)")
        + f" — {d['detail']}"
        for d in report["dimensions"]
    )
    positives = "\n".join(f"- {p['text']} [{p['evidence']}]" for p in report["positives"]) or "- none identified"
    negatives = "\n".join(f"- {n['text']} [{n['evidence']}]" for n in report["negatives"]) or "- none identified"
    recs = "\n".join(f"- {r['text']}" for r in report["recommendations"]) or "- none"
    return (
        f"Project: {report['project_name']}\n"
        f"Overall health score: {report['overall_score']}/100 ({report['status']})\n"
        f"Formula version: {report['formula_version']}\n"
        f"Data completeness: {report['data_completeness']}/100\n\n"
        f"DIMENSIONS:\n{dims}\n\n"
        f"POSITIVE FACTORS:\n{positives}\n\n"
        f"NEGATIVE FACTORS:\n{negatives}\n\n"
        f"RECOMMENDATIONS:\n{recs}\n\n"
        "Explain this health score in plain prose for a project manager. Do not introduce "
        "any number that is not written above."
    )


def explain_health(report: dict) -> tuple[str, str, str]:
    """Return (narrative, provider, model). Never raises."""
    try:
        manager = get_provider_manager()
        text, provider, model = manager.generate(
            _explanation_prompt(report), system=EXPLANATION_SYSTEM, temperature=0.2
        )
        return (text or "").strip()[:MAX_NARRATIVE_CHARS], provider, model
    except AIProviderError as exc:
        logger.warning("Health explanation provider unavailable: %s", exc)
        return "", "", ""
    except Exception as exc:  # noqa: BLE001 - explanation is strictly optional
        logger.warning("Health explanation failed: %s", exc)
        return "", "", ""


def get_health(
    db: Session,
    project: Project,
    *,
    persist: bool = True,
    explain: bool = True,
    created_by: int | None = None,
    audit_request=None,
) -> dict:
    """Compute (and optionally store) the deterministic health report."""
    report = score_project(db, project)
    report["formula"] = FORMULA_TEXT

    if explain:
        narrative, provider, model = explain_health(report)
        report["narrative"] = narrative
        report["narrative_provider"] = provider
        report["narrative_model"] = model

    if persist:
        snapshot = HealthSnapshot(
            project_id=project.id,
            overall_score=int(round(report["overall_score"])),
            status=report["status"],
            dimensions=[
                {
                    "key": d["key"],
                    "label": d["label"],
                    "score": d["score"],
                    "weight": d["weight"],
                    "supported": d["supported"],
                    "detail": d["detail"],
                    "facts": d["facts"],
                }
                for d in report["dimensions"]
            ],
            formula_version=FORMULA_VERSION,
            inputs=report["inputs"],
            positives=report["positives"],
            negatives=report["negatives"],
            recommendations=report["recommendations"],
            narrative=report["narrative"],
            narrative_provider=report["narrative_provider"],
            narrative_model=report["narrative_model"],
            data_completeness=report["data_completeness"],
        )
        db.add(snapshot)
        db.commit()
        db.refresh(snapshot)
        report["snapshot_id"] = snapshot.id
        report["computed_at"] = snapshot.created_at
        report["created_by"] = created_by

    return report


def latest_snapshot(db: Session, project_id: int) -> HealthSnapshot | None:
    return (
        db.query(HealthSnapshot)
        .filter(HealthSnapshot.project_id == project_id)
        .order_by(HealthSnapshot.id.desc())
        .first()
    )


def history(db: Session, project_id: int, limit: int = 20) -> list[HealthSnapshot]:
    return (
        db.query(HealthSnapshot)
        .filter(HealthSnapshot.project_id == project_id)
        .order_by(HealthSnapshot.id.desc())
        .limit(limit)
        .all()
    )


def snapshot_to_dict(s: HealthSnapshot, *, include_dimensions: bool = True) -> dict:
    out = {
        "id": s.id,
        "project_id": s.project_id,
        "overall_score": s.overall_score,
        "status": s.status,
        "formula_version": s.formula_version,
        "data_completeness": s.data_completeness,
        "positives": s.positives or [],
        "negatives": s.negatives or [],
        "recommendations": s.recommendations or [],
        "narrative": s.narrative or "",
        "narrative_provider": s.narrative_provider or "",
        "narrative_model": s.narrative_model or "",
        "created_at": s.created_at,
    }
    if include_dimensions:
        out["dimensions"] = s.dimensions or []
    return out


def snapshot_from_row(s: HealthSnapshot) -> dict:
    """Shape a stored snapshot like a live report, for the history view."""
    report = snapshot_to_dict(s)
    report["dimensions"] = s.dimensions or []
    report["skipped_dimensions"] = [
        d.get("key") for d in (s.dimensions or []) if not d.get("supported", True)
    ]
    report["has_analysis"] = True
    report["stored"] = True
    return report


def record_audit(
    db: Session,
    project_id: int,
    user_id: int | None,
    detail: str,
    request=None,
) -> None:
    log_event(
        db,
        user_id=user_id,
        user_email="",
        action="project_health_scored",
        resource_type="project",
        resource_id=str(project_id),
        project_id=project_id,
        detail=detail,
        request=request,
    )


__all__ = [
    "EXPLANATION_SYSTEM",
    "get_health",
    "latest_snapshot",
    "history",
    "snapshot_to_dict",
    "snapshot_from_row",
    "record_audit",
]
