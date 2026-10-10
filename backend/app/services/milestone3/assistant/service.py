"""Conversational Project Assistant (Milestone 3.3).

Grounding contract
------------------
An answer may only be produced from material that already exists for *this*
project:

1. **Conversation memory** - the turns of the current conversation.
2. **Structured project data** - the existing Milestone 1/2 rows (project
   record, risks, blockers, tasks, documents, scope/forecast insights).
3. **RAG retrieval** - chunks from this project's own vector collection via
   ``app.services.rag`` (no second retrieval system).

If none of the three contain the answer, the assistant replies with the exact
sentence required by the specification and asks for the missing document:

    I couldn't find sufficient evidence in the uploaded project documents to answer this.

It never fabricates a project fact, and it can never read another project's
data: every query is filtered by the conversation's ``project_id``.
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.models import (
    Blocker,
    Project,
    ProjectDocument,
    ProjectInsight,
    Risk,
    Task,
    User,
    utcnow,
)
from app.models_m3 import AssistantConversation, AssistantMessage
from app.services.ai.prompts import ASSISTANT_SYSTEM
from app.services.ai.providers import AIProviderError, get_provider_manager
from app.services.milestone3.common import (
    NOT_SPECIFIED,
    level,
    severity_score,
)
from app.services.rag import build_context, retrieve_chunks, sources_for_chunks

logger = logging.getLogger("m3.assistant")

#: The exact wording required by the specification. Do not reword.
INSUFFICIENT_EVIDENCE = (
    "I couldn't find sufficient evidence in the uploaded project documents to answer this."
)

PROVIDER_UNAVAILABLE = (
    "The AI provider is currently unavailable. Please try again later."
)

ASSISTANT_SYSTEM_M3 = (
    ASSISTANT_SYSTEM
    + "\n\nADDITIONAL RULES FOR THIS CONVERSATION:\n"
    "- The PROJECT DATA and CONVERSATION HISTORY blocks are also evidence. Use them.\n"
    "- Answer only from the supplied context. If the context does not contain the "
    "answer, reply with exactly: " + INSUFFICIENT_EVIDENCE + "\n"
    "- When a fact is absent, write: " + NOT_SPECIFIED + "\n"
    "- Cite the document name and page/row for any claim taken from a source block.\n"
    "- Keep answers concise and specific to this project. Never speculate about "
    "other projects."
)

MAX_HISTORY_TURNS = 8
MAX_CONTEXT_CHARS = 12000
MAX_QUESTION_CHARS = 4000

#: Keywords that let a question be answered from structured rows alone, so a
#: count question ("how many open risks?") does not depend on the vector store.
STRUCTURED_HINTS = {
    "risk": ("risk", "risks", "threat"),
    "blocker": ("blocker", "blockers", "stuck", "impediment"),
    "task": ("task", "tasks", "todo", "to do", "action item", "action items", "progress"),
    "document": ("document", "documents", "upload", "file", "files", "attachment"),
    "schedule": ("schedule", "timeline", "deadline", "due", "on track", "delivery date"),
    "scope": ("scope", "goal", "objective", "deliverable", "milestone", "requirement"),
}


# --------------------------------------------------------------------------- #
# Conversation persistence
# --------------------------------------------------------------------------- #


def _is_ai_task(db: Session, project_id: int, user_id: int | None) -> bool:
    """Only admins and the project manager can ask the AI about a project."""
    if user_id is None:
        return False
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        return False
    # After removing the role system, every authenticated user may query
    # project-grounded assistant; membership is no longer a hard gate.
    return True


def create_conversation(
    db: Session, project: Project, user_id: int | None, title: str = ""
) -> AssistantConversation:
    conv = AssistantConversation(
        project_id=project.id,
        title=(title or "Project conversation").strip()[:255],
        created_by=user_id,
    )
    db.add(conv)
    db.commit()
    db.refresh(conv)
    return conv


def get_conversation(db: Session, project_id: int, conversation_id: int) -> AssistantConversation | None:
    """Scoped by project_id so a conversation id from another project 404s."""
    return (
        db.query(AssistantConversation)
        .filter(
            AssistantConversation.id == conversation_id,
            AssistantConversation.project_id == project_id,
        )
        .first()
    )


def list_conversations(
    db: Session, project_id: int, user_id: int | None, limit: int = 25
) -> list[AssistantConversation]:
    q = db.query(AssistantConversation).filter(AssistantConversation.project_id == project_id)
    if user_id is not None:
        q = q.filter(AssistantConversation.created_by == user_id)
    return q.order_by(AssistantConversation.updated_at.desc(), AssistantConversation.id.desc()).limit(limit).all()


def delete_conversation(db: Session, project_id: int, conversation_id: int) -> bool:
    conv = get_conversation(db, project_id, conversation_id)
    if not conv:
        return False
    db.delete(conv)
    db.commit()
    return True


def _add_message(
    db: Session, conv: AssistantConversation, role: str, content: str, **fields
) -> AssistantMessage:
    msg = AssistantMessage(
        conversation_id=conv.id,
        project_id=conv.project_id,
        role=role,
        content=content,
        **fields,
    )
    db.add(msg)
    # Bump the thread timestamp explicitly: the column default only applies at
    # INSERT time, so reading it back from the pending object would be None.
    conv.updated_at = utcnow()
    db.commit()
    db.refresh(msg)
    return msg


# --------------------------------------------------------------------------- #
# Evidence assembly
# --------------------------------------------------------------------------- #


def structured_context(db: Session, project: Project) -> str:
    """Render the existing structured rows as a compact, factual block."""
    lines = [
        f"Project: {project.name}",
        f"Status: {project.status or NOT_SPECIFIED}",
        f"Priority: {project.priority or NOT_SPECIFIED}",
        f"Objective: {(project.objective or '').strip() or NOT_SPECIFIED}",
        f"Start: {project.start_date.date().isoformat() if project.start_date else NOT_SPECIFIED}",
        f"Expected end: {project.expected_end_date.date().isoformat() if project.expected_end_date else NOT_SPECIFIED}",
        "",
    ]

    risks = db.query(Risk).filter(Risk.project_id == project.id).order_by(Risk.id.asc()).all()
    lines.append(f"RISKS ({len(risks)} recorded):")
    if risks:
        for r in risks:
            score = severity_score(r.severity, r.probability, r.impact)
            lines.append(
                f"- [{r.status}] {r.title} | severity {r.severity}, probability "
                f"{r.probability}, impact {r.impact}, score {score}/25 | "
                f"{(r.description or '').strip()[:200] or NOT_SPECIFIED}"
            )
    else:
        lines.append(f"- {NOT_SPECIFIED}")

    blockers = db.query(Blocker).filter(Blocker.project_id == project.id).order_by(Blocker.id.asc()).all()
    lines.append("")
    lines.append(f"BLOCKERS ({len(blockers)} recorded):")
    if blockers:
        for b in blockers:
            lines.append(
                f"- [{b.status}] {b.title} | severity {b.severity}, owner "
                f"{(b.owner or '').strip() or NOT_SPECIFIED} | "
                f"{(b.description or '').strip()[:200] or NOT_SPECIFIED}"
            )
    else:
        lines.append(f"- {NOT_SPECIFIED}")

    tasks = db.query(Task).filter(Task.project_id == project.id).order_by(Task.id.asc()).all()
    lines.append("")
    lines.append(f"TASKS ({len(tasks)} recorded):")
    if tasks:
        by_status: dict[str, int] = {}
        for t in tasks:
            by_status[t.status] = by_status.get(t.status, 0) + 1
        lines.append(f"- Status breakdown: {', '.join(f'{k}: {v}' for k, v in sorted(by_status.items()))}")
        for t in tasks[:60]:
            assignee = db.query(User.name).filter(User.id == t.assigned_to).first() if t.assigned_to else None
            lines.append(
                f"- [{t.status}] {t.title} | priority {t.priority}, owner "
                f"{(assignee[0] if assignee else '') or NOT_SPECIFIED}, due "
                f"{t.due_date.date().isoformat() if t.due_date else NOT_SPECIFIED}"
            )
        if len(tasks) > 60:
            lines.append(f"- ... and {len(tasks) - 60} more tasks")
    else:
        lines.append(f"- {NOT_SPECIFIED}")

    docs = db.query(ProjectDocument).filter(ProjectDocument.project_id == project.id).all()
    lines.append("")
    lines.append(f"UPLOADED DOCUMENTS ({len(docs)}):")
    if docs:
        for d in docs:
            lines.append(
                f"- {d.original_name} ({d.file_type}, {d.status}, {d.chunk_count} indexed chunks)"
            )
    else:
        lines.append(f"- {NOT_SPECIFIED}")

    insights = (
        db.query(ProjectInsight)
        .filter(ProjectInsight.project_id == project.id)
        .order_by(ProjectInsight.id.desc())
        .limit(40)
        .all()
    )
    if insights:
        lines.append("")
        lines.append("MILESTONE 2 ANALYSIS (most recent entries):")
        for row in insights:
            lines.append(f"- [{row.agent}/{row.category}] {row.title or row.summary}".rstrip())

    return "\n".join(lines)


def _history_text(conv: AssistantConversation) -> str:
    turns = [m for m in conv.messages][-MAX_HISTORY_TURNS:]
    if not turns:
        return ""
    out = []
    for m in turns:
        out.append(f"{m.role.upper()}: {m.content}")
    return "\n".join(out)


def _wants_structured(question: str) -> set[str]:
    q = (question or "").lower()
    return {key for key, words in STRUCTURED_HINTS.items() if any(w in q for w in words)}


# --------------------------------------------------------------------------- #
# Answering
# --------------------------------------------------------------------------- #


def answer(
    db: Session,
    project: Project,
    question: str,
    *,
    conversation: AssistantConversation | None = None,
    user_id: int | None = None,
    top_k: int | None = None,
    persist: bool = True,
) -> dict:
    """Answer one question, grounded in memory + structured data + RAG."""
    question = (question or "").strip()[:MAX_QUESTION_CHARS]
    if not question:
        return _result(INSUFFICIENT_EVIDENCE, [], grounded=False)

    chunks: list[dict] = []
    retrieval_error = ""
    try:
        chunks = retrieve_chunks(project.id, question, top_k=top_k)
    except Exception as exc:  # noqa: BLE001 - vector store must never break the chat
        retrieval_error = str(exc)
        logger.warning("Assistant retrieval failed for project %s: %s", project.id, exc)

    sources = sources_for_chunks(chunks)
    structured = structured_context(db, project)
    history = _history_text(conversation) if conversation else ""
    hints = _wants_structured(question)

    # If neither retrieval nor a relevant structured table has anything, the
    # question cannot be answered from project evidence.
    has_structured_match = bool(hints) or "document" in hints
    if not chunks and not has_structured_match and not history:
        return _persist(
            db,
            conversation,
            question,
            _result(INSUFFICIENT_EVIDENCE, [], grounded=False, used_structured=False),
            user_id,
            persist,
        )

    prompt_parts = [
        f"CONVERSATION HISTORY:\n{history or '(no earlier turns)'}",
        "",
        f"PROJECT DATA:\n{structured}",
        "",
        f"DOCUMENT CONTEXT:\n{build_context(chunks) if chunks else '(no matching document sections)'}",
        "",
        f"QUESTION: {question}",
        "",
        "Answer using only the context above. Cite document names for document-based "
        "claims. If the context does not answer the question, reply with exactly: "
        + INSUFFICIENT_EVIDENCE,
    ]
    prompt = "\n".join(prompt_parts)

    try:
        answer_text, provider, model = get_provider_manager().generate(
            prompt, system=ASSISTANT_SYSTEM_M3, temperature=0.2
        )
    except AIProviderError as exc:
        logger.error("Assistant provider failed: %s", exc)
        return _persist(
            db,
            conversation,
            question,
            _result(
                PROVIDER_UNAVAILABLE,
                sources,
                grounded=False,
                error=str(exc),
                retrieval_error=retrieval_error,
            ),
            user_id,
            persist,
        )

    answer_text = (answer_text or "").strip()
    grounded = bool(chunks) or bool(hints) or bool(history)
    # The model may still decline; treat its own exact refusal as ungrounded.
    if INSUFFICIENT_EVIDENCE.lower() in answer_text.lower():
        answer_text = INSUFFICIENT_EVIDENCE
        grounded = False

    return _persist(
        db,
        conversation,
        question,
        _result(
            answer_text,
            sources,
            grounded=grounded,
            used_structured=bool(hints),
            used_history=bool(history),
            provider=provider,
            model=model,
            retrieval_error=retrieval_error,
            retrieved_count=len(chunks),
        ),
        user_id,
        persist,
    )


def _result(
    text: str,
    sources: list[dict],
    *,
    grounded: bool,
    used_structured: bool = False,
    used_history: bool = False,
    provider: str = "",
    model: str = "",
    error: str = "",
    retrieval_error: str = "",
    retrieved_count: int = 0,
) -> dict:
    return {
        "answer": text,
        "sources": sources,
        "grounded": grounded,
        "insufficient_evidence": text == INSUFFICIENT_EVIDENCE,
        "used_structured_data": used_structured,
        "used_conversation_context": used_history,
        "provider": provider,
        "model": model,
        "error": error,
        "retrieval_error": retrieval_error,
        "retrieved_count": retrieved_count,
    }


def _persist(
    db: Session,
    conv: AssistantConversation | None,
    question: str,
    result: dict,
    user_id: int | None,
    persist: bool,
) -> dict:
    if not persist or conv is None:
        return result
    _add_message(db, conv, "user", question)
    _add_message(
        db,
        conv,
        "assistant",
        result["answer"],
        evidence=result.get("sources", []),
        sources=result.get("sources", []),
        grounded=result["grounded"],
        used_structured_data=result["used_structured_data"],
        used_conversation_context=result["used_conversation_context"],
        insufficient_evidence=result["insufficient_evidence"],
        provider=result.get("provider", ""),
        model=result.get("model", ""),
        error=result.get("error") or None,
    )
    result["conversation_id"] = conv.id
    return result


def message_to_dict(m: AssistantMessage) -> dict:
    return {
        "id": m.id,
        "conversation_id": m.conversation_id,
        "role": m.role,
        "content": m.content,
        "sources": m.sources or [],
        "grounded": m.grounded,
        "insufficient_evidence": m.insufficient_evidence,
        "used_structured_data": m.used_structured_data,
        "used_conversation_context": m.used_conversation_context,
        "provider": m.provider,
        "model": m.model,
        "error": m.error,
        "created_at": m.created_at,
    }


def conversation_to_dict(c: AssistantConversation, include_messages: bool = True) -> dict:
    out = {
        "id": c.id,
        "project_id": c.project_id,
        "title": c.title,
        "created_at": c.created_at,
        "updated_at": c.updated_at,
        "message_count": len(c.messages),
    }
    if include_messages:
        out["messages"] = [message_to_dict(m) for m in c.messages]
    return out


__all__ = [
    "INSUFFICIENT_EVIDENCE",
    "PROVIDER_UNAVAILABLE",
    "ASSISTANT_SYSTEM_M3",
    "answer",
    "create_conversation",
    "get_conversation",
    "list_conversations",
    "delete_conversation",
    "structured_context",
    "message_to_dict",
    "conversation_to_dict",
]
