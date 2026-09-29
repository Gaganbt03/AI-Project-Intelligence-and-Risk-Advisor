from __future__ import annotations

from sqlalchemy.orm import Session

from app.config import get_settings
from app.services.embeddings import get_embedding_provider
from app.services.vector_store import get_vector_store


def format_citation(item: dict) -> str:
    """Human-readable citation for a retrieved chunk."""
    doc = item.get("original_name") or "Unknown document"
    parts = [doc]
    if item.get("page"):
        parts.append(f"Page {item['page']}")
    if item.get("section"):
        parts.append(item["section"])
    if item.get("row"):
        parts.append(f"Row {item['row']}")
    return " — ".join(parts)


def retrieve_chunks(project_id: int, query: str, top_k: int | None = None) -> list[dict]:
    settings = get_settings()
    provider = get_embedding_provider()
    embedding = provider.embed_one(query)
    vs = get_vector_store()
    return vs.query(project_id, embedding, top_k=top_k or settings.AI_MAX_CHUNKS)


def build_context(chunks: list[dict]) -> str:
    blocks = []
    for i, chunk in enumerate(chunks, start=1):
        source = format_citation(chunk)
        blocks.append(f"[{i}] SOURCE: {source}\n{chunk['text']}")
    return "\n\n".join(blocks)


def sources_for_chunks(chunks: list[dict]) -> list[dict]:
    """Deduplicated source list for citations displayed to the user."""
    seen: dict[str, dict] = {}
    for chunk in chunks:
        key = format_citation(chunk)
        entry = {
            "label": key,
            "original_name": chunk.get("original_name", ""),
            "page": chunk.get("page", ""),
            "section": chunk.get("section", ""),
            "row": chunk.get("row", ""),
            "document_id": chunk.get("document_id", ""),
        }
        seen[key] = entry
    return list(seen.values())


def project_document_summary(db: Session, project_id: int) -> dict:
    """Counts used by agents and forecasts."""
    from app.models import Blocker, ProjectDocument, Risk, Task

    docs = db.query(ProjectDocument).filter(ProjectDocument.project_id == project_id).count()
    open_risks = (
        db.query(Risk)
        .filter(Risk.project_id == project_id, Risk.status.in_(["Open", "In Progress"]))
        .count()
    )
    open_blockers = (
        db.query(Blocker)
        .filter(Blocker.project_id == project_id, Blocker.status.in_(["Open", "In Progress"]))
        .count()
    )
    tasks = db.query(Task).filter(Task.project_id == project_id).count()
    done_tasks = db.query(Task).filter(Task.project_id == project_id, Task.status == "Completed").count()
    pending_tasks = (
        db.query(Task)
        .filter(Task.project_id == project_id, Task.status.in_(["Pending", "In Progress"]))
        .count()
    )
    return {
        "documents": docs,
        "open_risks": open_risks,
        "open_blockers": open_blockers,
        "total_tasks": tasks,
        "completed_tasks": done_tasks,
        "pending_tasks": pending_tasks,
        "completion_rate": round((done_tasks / tasks) * 100, 1) if tasks else 0.0,
    }