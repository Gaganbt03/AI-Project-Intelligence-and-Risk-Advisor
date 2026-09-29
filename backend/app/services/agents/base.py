from __future__ import annotations

from typing import Type, TypeVar

from sqlalchemy.orm import Session

from app.services.ai.providers import ProviderManager, get_provider_manager
from app.services.ai.prompts import ANALYSIS_GUIDE
from app.services.ai.repair import parse_and_validate
from app.services.ai.schemas import SourceRef
from app.services.rag import build_context, retrieve_chunks, sources_for_chunks

T = TypeVar("T")

from pydantic import BaseModel

M = TypeVar("M", bound=BaseModel)

MAX_ATTEMPTS = 2


class AgentError(Exception):
    pass


class BaseAgent:
    """Shared RAG + generation logic for all agents."""

    name = "base"
    schema_model: Type[M] | None = None
    system_prompt = ""
    default_queries: list[str] = []

    def __init__(self) -> None:
        self.manager: ProviderManager = get_provider_manager()

    def retrieve_context(self, project_id: int, queries: list[str] | None = None) -> list[dict]:
        queries = queries or self.default_queries
        chunks: list[dict] = []
        seen: set[str] = set()
        for q in queries:
            for chunk in retrieve_chunks(project_id, q):
                key = (chunk.get("vector_id") or chunk.get("text", "")[:80])
                if key in seen:
                    continue
                seen.add(key)
                chunks.append(chunk)
        return chunks

    def call_llm(self, prompt: str, temperature: float | None = 0.1) -> tuple[str, str, str]:
        """Return (text, provider, model) with failover. Agents always request JSON mode."""
        return self.manager.generate(prompt, system=self.system_prompt, temperature=temperature, json_mode=True)

    def run_with_retry(self, prompt: str, temperature: float | None = None) -> M | None:
        """Generate and validate; retry once on invalid output. Returns validated model or None."""
        if not self.schema_model:
            raise AgentError("Agent has no schema_model set.")
        text, provider, model = self.call_llm(prompt, temperature=temperature)
        parsed = parse_and_validate(text, self.schema_model)
        if parsed is not None:
            parsed.__provider = provider  # type: ignore[attr-defined]
            parsed.__model = model  # type: ignore[attr-defined]
            return parsed
        # Second attempt: deterministic temperature gives more reliable JSON on Qwen2.5.
        text2, provider2, model2 = self.call_llm(prompt, temperature=0.0)
        parsed = parse_and_validate(text2, self.schema_model)
        if parsed is not None:
            parsed.__provider = provider  # type: ignore[attr-defined]
            parsed.__model = model  # type: ignore[attr-defined]
            return parsed
        return None

    def context_source_refs(self, chunks: list[dict]) -> list[SourceRef]:
        return [
            SourceRef(
                document=chunk.get("original_name") or "",
                page=_to_int_or_none(chunk.get("page")),
                section=chunk.get("section") or None,
                row=_to_int_or_none(chunk.get("row")),
                quote=chunk.get("text", "")[:400],
            )
            for chunk in chunks
        ]

    def run(self, db: Session, project_id: int) -> dict:
        raise NotImplementedError


def _to_int_or_none(v) -> int | None:
    try:
        if v in ("", None):
            return None
        return int(v)
    except (TypeError, ValueError):
        return None


def format_evidence_sources(chunks: list[dict]) -> str:
    return "\n".join(f"- {c.get('original_name', '?')}" for c in chunks)