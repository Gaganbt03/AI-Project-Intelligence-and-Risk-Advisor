"""Documentation Generation Agent — orchestrator for Milestone 3.1.

Public entry points:

* :func:`generate_document` — build one document type and persist it.
* :func:`generate_all`       — build all three types for a project.
* :func:`regenerate`         — rebuild an existing document in place.

Persistence notes
-----------------
Generated documents live in the ``generated_documents`` table, which is
completely separate from the ``documents`` table that holds user uploads.
Regeneration replaces the *content of a generated row*; it never touches, and
can never reach, an uploaded file.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models import Project
from app.models_m3 import GeneratedDocument
from app.services.audit import log_event
from app.services.milestone3.common import (
    DOC_ACTION_ITEMS,
    DOC_RISK_REGISTER,
    DOC_TYPES,
    DOC_USER_STORIES,
    INSUFFICIENT_EVIDENCE,
    doc_file_name,
)
from app.services.milestone3.documentation.action_items import build_action_items
from app.services.milestone3.documentation.evidence import build_documentation_context
from app.services.milestone3.documentation.renderer import render
from app.services.milestone3.documentation.risk_register import build_risk_register
from app.services.milestone3.documentation.schemas import EvidenceItem
from app.services.milestone3.documentation.user_stories import generate_user_stories
from app.services.milestone3.documentation.validator import Validator

logger = logging.getLogger("m3.documentation")

DOC_DESCRIPTIONS = {
    DOC_USER_STORIES: "User stories derived from the project's own documents and its recorded scope analysis.",
    DOC_RISK_REGISTER: "Risk register projected from the risks already recorded for this project.",
    DOC_ACTION_ITEMS: "Structured action item list built from existing tasks, AI action items and open blockers.",
}

#: Per-artifact field order shown in the UI, in the sequence the specification
#: requires. The Markdown renderer, the DOCX exporter and the frontend cards all
#: follow this, so every representation reads the same way.
ARTIFACT_FIELDS: dict[str, list[tuple[str, str]]] = {
    DOC_USER_STORIES: [
        ("as_a", "As a"),
        ("i_want", "I want"),
        ("so_that", "So that"),
        ("priority", "Priority"),
        ("status", "Status"),
        ("acceptance_criteria", "Acceptance criteria"),
        ("related_requirement", "Related requirement"),
        ("source_document", "Source document"),
        ("source_evidence", "Source evidence"),
    ],
    DOC_RISK_REGISTER: [
        ("description", "Description"),
        ("category", "Category"),
        ("probability", "Probability"),
        ("impact", "Impact"),
        ("risk_score", "Risk score"),
        ("severity_band", "Severity"),
        ("status", "Status"),
        ("owner", "Owner"),
        ("mitigation", "Mitigation"),
        ("contingency", "Contingency"),
        ("related_blockers", "Related blockers"),
        ("related_actions", "Related actions"),
        ("source_document", "Source document"),
        ("source_evidence", "Source evidence"),
    ],
    DOC_ACTION_ITEMS: [
        ("description", "Description"),
        ("owner", "Owner"),
        ("priority", "Priority"),
        ("due_date", "Due date"),
        ("status", "Status"),
        ("related_risk", "Related risk"),
        ("related_blocker", "Related blocker"),
        ("origin", "Origin"),
        ("source_document", "Source document"),
        ("source_evidence", "Source evidence"),
    ],
}

#: Key holding the identifier of each artifact, which is rendered as its heading
#: rather than as a row in the field list.
_ID_FIELD = {
    DOC_USER_STORIES: "id",
    DOC_RISK_REGISTER: "risk_id",
    DOC_ACTION_ITEMS: "action_id",
}

#: Key holding the human title of each artifact.
_TITLE_FIELD = {
    DOC_USER_STORIES: "title",
    DOC_RISK_REGISTER: "title",
    DOC_ACTION_ITEMS: "action",
}


class DocumentationError(Exception):
    pass


def _artifact_fields(doc_type: str, entry: dict) -> list[dict]:
    """Flatten one built entry into ``{label, value}`` pairs for the UI.

    Only the fields the specification lists are emitted, and the order is fixed,
    so the frontend never has to know each artifact's shape.
    """
    fields: list[dict] = []
    for key, label in ARTIFACT_FIELDS.get(doc_type, []):
        fields.append({"key": key, "label": label, "value": entry.get(key)})
    return fields


def _artifact_title(identifier: str, title: str) -> str:
    """Drop a leading reference that the heading already shows.

    Milestone 2 titles embed their own id (``R001 MQTT gateway reliability``).
    Rendering that under a ``R001`` heading reads as a duplication.

    >>> _artifact_title("R001", "R001 MQTT gateway reliability")
    'MQTT gateway reliability'
    >>> _artifact_title("A001", "Implement MQTT reconnect handling")
    'Implement MQTT reconnect handling'
    """
    clean = (title or "").strip()
    if identifier and clean.upper().startswith(f"{identifier.upper()} "):
        return clean[len(identifier) + 1:].strip() or clean
    return clean


def _build_artifacts(doc_type: str, data: dict) -> list[dict]:
    entries = data.get("entries") if doc_type != DOC_USER_STORIES else data.get("stories")
    entries = entries or []
    id_key = _ID_FIELD.get(doc_type, "id")
    title_key = _TITLE_FIELD.get(doc_type, "title")
    artifacts: list[dict] = []
    for entry in entries:
        identifier = entry.get(id_key, "")
        artifacts.append(
            {
                "id": identifier,
                "title": _artifact_title(identifier, entry.get(title_key, "")),
                "fields": _artifact_fields(doc_type, entry),
                "field_verdicts": entry.get("field_verdicts", {}),
                "conflicts": entry.get("conflicts", []) or [],
                "due_date_trace": entry.get("due_date_trace", {}) or {},
            }
        )
    return artifacts


def _build_payload(db: Session, project: Project, doc_type: str) -> dict:
    """Produce the structured payload for one document type.

    The risk register and action item list are computed purely from rows that
    already exist, so they never touch the vector store or the AI provider. Only
    user stories require retrieval, and ``generate_user_stories`` reuses the
    evidence it already collected instead of retrieving a second time.

    Every payload is finished with the same two keys, so the UI, the Markdown
    renderer and the DOCX exporter read one shape regardless of doc type:

    * ``artifacts`` - the entries flattened into labelled fields, in order
    * ``validation`` - the document-level summary, already produced by the builder
    """
    if doc_type == DOC_USER_STORIES:
        data = generate_user_stories(db, project)
        evidence = [
            EvidenceItem(**item) for item in data.get("evidence", []) if isinstance(item, dict)
        ]
        context = build_documentation_context(db, project, evidence=evidence)
    elif doc_type == DOC_RISK_REGISTER:
        data = build_risk_register(db, project)
        context = build_documentation_context(db, project)
    elif doc_type == DOC_ACTION_ITEMS:
        data = build_action_items(db, project)
        context = build_documentation_context(db, project)
    else:
        raise DocumentationError(f"Unknown documentation type: {doc_type}")

    data["context"] = {
        "has_documents": context.has_documents,
        "document_count": context.document_count,
        "source_agents": context.source_agents,
        "evidence": [e.model_dump() for e in context.evidence],
    }
    # Project due date resolved with document-first precedence. Never invented:
    # an explicit date in an uploaded document wins, otherwise the value entered
    # on the project record is used, otherwise it stays "Not specified".
    from app.services.pipeline import resolve_project_due_date

    data["project_due_date"] = resolve_project_due_date(db, project)
    data.setdefault("insufficient_evidence", False)
    data.setdefault("validation", Validator().finish().to_dict())
    data.setdefault("validation_notes", [])
    data["artifacts"] = _build_artifacts(doc_type, data)
    return data


def generate_document(
    db: Session,
    project: Project,
    doc_type: str,
    *,
    created_by: int | None = None,
    audit_request=None,
) -> GeneratedDocument:
    """Build one document type and upsert it. Returns the persisted row."""
    if doc_type not in DOC_TYPES:
        raise DocumentationError(
            f"Unknown documentation type '{doc_type}'. Expected one of {', '.join(DOC_TYPES)}."
        )

    payload = _build_payload(db, project, doc_type)
    content = render(doc_type, payload, project.name)
    file_name = doc_file_name(project.name, doc_type)

    existing = (
        db.query(GeneratedDocument)
        .filter(
            GeneratedDocument.project_id == project.id,
            GeneratedDocument.doc_type == doc_type,
        )
        .first()
    )

    if existing:
        existing.content = content
        existing.payload = payload
        existing.file_name = file_name
        existing.title = payload.get("title", "")
        existing.generation_count = (existing.generation_count or 0) + 1
        existing.insufficient_evidence = bool(payload.get("insufficient_evidence"))
        existing.provider = payload.get("provider", "") or ""
        existing.model = payload.get("model", "") or ""
        existing.error = payload.get("error")
        existing.updated_at = datetime.now(timezone.utc)
        doc = existing
    else:
        doc = GeneratedDocument(
            project_id=project.id,
            doc_type=doc_type,
            title=payload.get("title", ""),
            content=content,
            payload=payload,
            file_name=file_name,
            file_type="md",
            generation_count=1,
            insufficient_evidence=bool(payload.get("insufficient_evidence")),
            provider=payload.get("provider", "") or "",
            model=payload.get("model", "") or "",
            error=payload.get("error"),
            created_by=created_by,
        )
        db.add(doc)

    db.commit()
    db.refresh(doc)

    log_event(
        db,
        user_id=created_by,
        user_email="",
        action="generated_document_created",
        resource_type="generated_document",
        resource_id=str(doc.id),
        project_id=project.id,
        detail=f"{doc_type} ({'regenerated' if existing else 'created'})",
        request=audit_request,
    )
    return doc


def generate_all(
    db: Session,
    project: Project,
    *,
    created_by: int | None = None,
    audit_request=None,
) -> list[GeneratedDocument]:
    """Build all three document types. A failure in one never blocks the rest."""
    out: list[GeneratedDocument] = []
    for doc_type in DOC_TYPES:
        try:
            out.append(
                generate_document(
                    db, project, doc_type, created_by=created_by, audit_request=audit_request
                )
            )
        except Exception as exc:  # noqa: BLE001 - one type must not kill the batch
            logger.exception("Documentation generation failed for %s / %s", project.id, doc_type)
            db.rollback()
    return out


def regenerate(
    db: Session,
    doc: GeneratedDocument,
    *,
    created_by: int | None = None,
    audit_request=None,
) -> GeneratedDocument:
    """Rebuild an existing generated document in place.

    Only ``generated_documents`` rows reach this path; uploaded documents are
    immutable by design and are never passed in.
    """
    project = db.query(Project).filter(Project.id == doc.project_id).first()
    if not project:
        raise DocumentationError("Project not found.")
    # The audit entry is written by generate_document, which knows whether the
    # row was newly created or regenerated.
    return generate_document(
        db, project, doc.doc_type, created_by=created_by, audit_request=audit_request
    )


def list_documents(db: Session, project_id: int) -> list[GeneratedDocument]:
    return (
        db.query(GeneratedDocument)
        .filter(GeneratedDocument.project_id == project_id)
        .order_by(GeneratedDocument.doc_type.asc())
        .all()
    )


def get_document(db: Session, project_id: int, doc_id: int) -> GeneratedDocument | None:
    """Scoped lookup — the project_id filter is the isolation guarantee."""
    return (
        db.query(GeneratedDocument)
        .filter(GeneratedDocument.id == doc_id, GeneratedDocument.project_id == project_id)
        .first()
    )


def summarize(db: Session, project_id: int) -> dict:
    """Counts used by the project health / documentation UI."""
    docs = list_documents(db, project_id)
    return {
        "total": len(docs),
        "types": [d.doc_type for d in docs],
        "insufficient_evidence": sum(1 for d in docs if d.insufficient_evidence),
        "generated": [
            {
                "id": d.id,
                "doc_type": d.doc_type,
                "title": d.title,
                "file_name": d.file_name,
                "generation_count": d.generation_count,
                "insufficient_evidence": d.insufficient_evidence,
                "updated_at": d.updated_at,
                "provider": d.provider,
                "model": d.model,
            }
            for d in docs
        ],
        "descriptions": DOC_DESCRIPTIONS,
        "types_available": list(DOC_TYPES),
    }


__all__ = [
    "ARTIFACT_FIELDS",
    "DocumentationError",
    "DOC_DESCRIPTIONS",
    "generate_document",
    "generate_all",
    "regenerate",
    "list_documents",
    "get_document",
    "summarize",
    "INSUFFICIENT_EVIDENCE",
]
