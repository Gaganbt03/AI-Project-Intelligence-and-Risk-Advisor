"""Evidence collection for the Documentation Generation Agent.

Everything the generator is allowed to know is assembled here from records that
already exist in the Milestone 1/2 database:

* the project row itself (name, objective, description, dates, status),
* the Milestone 2 agent outputs stored in ``project_insights``
  (scope / risk / forecast / blocker / action),
* the structured ``risks`` and ``blockers`` tables,
* the ``tasks`` table (manual tasks *and* Milestone 2 AI-generated action items),
* retrieved document chunks from the **existing** project-scoped vector store.

No second retrieval system, no second embedding provider, no second vector
store — ``app.services.rag`` is used as-is.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import Blocker, Project, ProjectDocument, ProjectInsight, Risk, Task
from app.services.rag import format_citation, retrieve_chunks, sources_for_chunks
from app.services.milestone3.documentation.schemas import DocumentationContext, EvidenceItem

# Queries used to pull grounding context for user-story generation. They are
# deliberately aligned with the Milestone 2 agent queries so the same documents
# that produced scope and requirements also produce stories.
USER_STORY_QUERIES = [
    "project objective, goal, target users, stakeholders, personas",
    "functional requirements, user requirements, capabilities the system must provide",
    "deliverables, milestones, acceptance criteria, success measures",
    "roles, responsibilities, permissions, who uses this system",
]


def _to_int(value) -> int | None:
    try:
        if value in ("", None):
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def evidence_from_chunks(chunks: list[dict]) -> list[EvidenceItem]:
    """Convert RAG chunks into de-duplicated, user-facing citations."""
    seen: set[str] = set()
    out: list[EvidenceItem] = []
    for c in chunks:
        item = EvidenceItem(
            document=c.get("original_name") or "",
            page=_to_int(c.get("page")),
            section=c.get("section") or None,
            row=_to_int(c.get("row")),
            quote=(c.get("text") or "")[:400],
        )
        key = item.citation()
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def retrieve_user_story_context(project_id: int) -> list[dict]:
    """RAG retrieval for user-story grounding. Reuses the Milestone 1 pipeline
    and is hard-scoped to this project's collection."""
    chunks: list[dict] = []
    seen: set[str] = set()
    for query in USER_STORY_QUERIES:
        for chunk in retrieve_chunks(project_id, query):
            key = chunk.get("vector_id") or (chunk.get("text") or "")[:80]
            if key in seen:
                continue
            seen.add(key)
            chunks.append(chunk)
    return chunks


def scope_facts(db: Session, project_id: int) -> dict:
    """The Milestone 2 Scope Agent output, read back as plain facts."""
    rows = (
        db.query(ProjectInsight)
        .filter(ProjectInsight.project_id == project_id, ProjectInsight.agent == "scope")
        .order_by(ProjectInsight.id.desc())
        .all()
    )
    # Newest run wins: walk from the end and keep the first (most recent)
    # occurrence of each category.
    facts: dict[str, list[str]] = {}
    goal = ""
    for row in reversed(rows):
        key = row.category
        if key == "project_goal":
            if not goal:
                goal = row.summary or row.title or ""
            continue
        if key not in facts:
            facts[key] = []
        label = row.title or row.summary
        if label and label not in facts[key]:
            facts[key].append(label)
    return {
        "project_goal": goal,
        "scope": facts.get("scope", []),
        "out_of_scope": facts.get("out_of_scope", []),
        "deliverables": facts.get("deliverable", []),
        "milestones": facts.get("milestone", []),
        "timeline": facts.get("timeline", []),
        "responsibilities": facts.get("responsibility", []),
        "technologies": facts.get("technology", []),
        "requirements": facts.get("requirement", []),
    }


def forecast_fact(db: Session, project_id: int) -> dict:
    """Latest Milestone 2 Delivery Forecast payload (used as schedule context)."""
    row = (
        db.query(ProjectInsight)
        .filter(ProjectInsight.project_id == project_id, ProjectInsight.agent == "forecast")
        .order_by(ProjectInsight.id.desc())
        .first()
    )
    return dict(row.payload or {}) if row else {}


def existing_risks(db: Session, project_id: int) -> list[Risk]:
    return (
        db.query(Risk)
        .filter(Risk.project_id == project_id)
        .order_by(Risk.id.asc())
        .all()
    )


def existing_blockers(db: Session, project_id: int) -> list[Blocker]:
    return (
        db.query(Blocker)
        .filter(Blocker.project_id == project_id)
        .order_by(Blocker.id.asc())
        .all()
    )


def existing_tasks(db: Session, project_id: int) -> list[Task]:
    return (
        db.query(Task)
        .filter(Task.project_id == project_id)
        .order_by(Task.id.asc())
        .all()
    )


def processed_documents(db: Session, project_id: int) -> list[ProjectDocument]:
    return (
        db.query(ProjectDocument)
        .filter(
            ProjectDocument.project_id == project_id,
            ProjectDocument.status == "Processed",
        )
        .order_by(ProjectDocument.id.asc())
        .all()
    )


def build_documentation_context(
    db: Session,
    project: Project,
    chunks: list[dict] | None = None,
    evidence: list[EvidenceItem] | None = None,
) -> DocumentationContext:
    """Assemble the audit context stored alongside every generated document.

    ``chunks`` is only needed when RAG was actually used to build the document.
    The deterministic document types (risk register, action items) pass neither
    chunks nor evidence, so generating them never requires the vector store or
    the embedding provider to be available.
    """
    agents = [
        row.agent
        for row in (
            db.query(ProjectInsight.agent)
            .filter(ProjectInsight.project_id == project.id)
            .distinct()
            .all()
        )
    ]
    docs = processed_documents(db, project.id)
    if evidence is None:
        evidence = evidence_from_chunks(chunks or [])
    return DocumentationContext(
        evidence=evidence,
        has_documents=bool(chunks) or bool(docs),
        document_count=len(docs),
        source_agents=sorted({a for a in agents if a}),
    )


def format_block_context(chunks: list[dict], limit: int = 8) -> str:
    blocks = []
    for i, c in enumerate(chunks[:limit], start=1):
        blocks.append(f"[{i}] SOURCE: {format_citation(c)}\n{c.get('text', '')}")
    return "\n\n".join(blocks)


def source_labels_for(db: Session, project_id: int) -> dict[int, str]:
    """document_id -> original_name, for attributing existing rows to a file."""
    rows = (
        db.query(ProjectDocument.id, ProjectDocument.original_name)
        .filter(ProjectDocument.project_id == project_id)
        .all()
    )
    return {r[0]: r[1] for r in rows}


def structured_source_ref(chunks: list[dict]) -> list[dict]:
    """The lightweight source list shape the frontend already renders."""
    return sources_for_chunks(chunks)


__all__ = [
    "USER_STORY_QUERIES",
    "evidence_from_chunks",
    "retrieve_user_story_context",
    "scope_facts",
    "forecast_fact",
    "existing_risks",
    "existing_blockers",
    "existing_tasks",
    "processed_documents",
    "build_documentation_context",
    "format_block_context",
    "source_labels_for",
    "structured_source_ref",
]
