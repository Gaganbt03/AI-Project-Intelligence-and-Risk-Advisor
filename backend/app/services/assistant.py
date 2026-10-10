from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.services.ai.providers import AIProviderError, get_provider_manager
from app.services.ai.prompts import ANALYSIS_GUIDE, ASSISTANT_SYSTEM
from app.services.rag import build_context, retrieve_chunks, sources_for_chunks

logger = logging.getLogger("assistant")

NOT_FOUND_MSG = "I couldn't find sufficient information in the uploaded project documents."


def ask(project_id: int, question: str, top_k: int | None = None) -> dict:
    """Answer a project-scoped question with RAG retrieval. Never cross-project."""
    chunks = retrieve_chunks(project_id, question, top_k=top_k)
    sources = sources_for_chunks(chunks)

    if not chunks:
        return {
            "answer": NOT_FOUND_MSG,
            "sources": [],
            "provider": "",
            "model": "",
            "grounded": False,
        }

    context = build_context(chunks)
    prompt = (
        f"Question: {question}\n\nUse the DOCUMENT CONTEXT below. "
        "If your answer relies on a specific source, mention it (file name and page/row). "
        "Do not invent anything not present in the context."
        + ANALYSIS_GUIDE.format(context=context)
    )

    manager = get_provider_manager()
    try:
        answer, provider, model = manager.generate(prompt, system=ASSISTANT_SYSTEM, temperature=0.2)
    except AIProviderError as exc:
        logger.error("Assistant provider failed: %s", exc)
        return {
            "answer": "The AI provider is currently unavailable. Please try again later.",
            "sources": sources,
            "provider": "",
            "model": "",
            "grounded": False,
            "error": str(exc),
        }

    return {
        "answer": answer,
        "sources": sources,
        "provider": provider,
        "model": model,
        "grounded": bool(chunks),
        "retrieved_count": len(chunks),
    }