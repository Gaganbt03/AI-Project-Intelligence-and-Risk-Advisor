"""User Story generation — the only LLM-assisted documentation artefact.

Design rules, in order of importance:

1. **Grounding.** The model sees only retrieved chunks from *this* project's
   vector collection plus the Milestone 2 scope output. It cannot reach another
   project's data.
2. **No invention.** The prompt forbids requirements that are not present, and
   every field is optional in the schema. A story without a goal is dropped.
3. **Explicit insufficiency.** When the retrieved context contains no usable
   requirements, the generator returns zero stories and the document states
   "Insufficient evidence in uploaded project documents." — it does not fall
   back to generic or boilerplate stories.
4. **Provider reuse.** Generation goes through the existing
   ``ProviderManager`` (Ollama → Groq → Gemini → OpenRouter → Hugging Face),
   including model auto-discovery and the circuit breaker.
"""

from __future__ import annotations

import json
import logging

from sqlalchemy.orm import Session

from app.models import Project
from app.services.ai.providers import AIProviderError, get_provider_manager
from app.services.ai.repair import parse_and_validate
from app.services.milestone3.common import INSUFFICIENT_EVIDENCE
from app.services.milestone3.documentation.evidence import (
    evidence_from_chunks,
    format_block_context,
    retrieve_user_story_context,
    scope_facts,
)
from app.services.milestone3.documentation import validator as validation
from app.services.milestone3.documentation.schemas import (
    EvidenceItem,
    UserStory,
    UserStoryResult,
)

logger = logging.getLogger("m3.documentation.user_stories")

MAX_STORIES = 12

USER_STORY_SYSTEM = (
    "You are a senior business analyst writing user stories for an EXISTING project. "
    "You work ONLY from the DOCUMENT CONTEXT provided. Every requirement you write must be "
    "traceable to something stated in that context.\n\n"
    "HARD RULES:\n"
    "- Never invent a requirement, feature, user role, benefit or acceptance criterion.\n"
    "- If the context does not state a benefit, leave \"benefit\" empty.\n"
    "- If the context does not state acceptance criteria, return an empty list.\n"
    "- Do not produce generic or template stories. If the context supports no real story, "
    'return {"user_stories": []}.\n'
    "- Copy the exact file name from the SOURCE line when you cite a document.\n\n"
    "Respond with a SINGLE JSON object only (no markdown, no prose):\n"
    '{"user_stories": [{"title": str, "role": str, "goal": str, "benefit": str, '
    '"acceptance_criteria": [str], "priority": "Low|Medium|High|Critical", '
    '"source_document": str, "evidence": str}]}\n'
    "Format goal as the middle clause of \"I want ...\" and benefit as the "
    "\"so that ...\" clause. The role must be a concrete actor named in the context."
)


def _build_prompt(project: Project, chunks: list[dict], scope: dict) -> str:
    scope_lines = [
        f"Project: {project.name}",
        f"Objective (project record): {project.objective or 'Not recorded.'}",
        f"Description (project record): {project.description or 'Not recorded.'}",
        "",
        "MILESTONE 2 SCOPE AGENT OUTPUT (already extracted from these same documents):",
        f"- Project goal: {scope.get('project_goal') or 'Not recorded.'}",
        f"- Scope items: {', '.join(scope.get('scope', [])) or 'None recorded.'}",
        f"- Deliverables: {', '.join(scope.get('deliverables', [])) or 'None recorded.'}",
        f"- Requirements: {', '.join(scope.get('requirements', [])) or 'None recorded.'}",
        f"- Responsibilities: {', '.join(scope.get('responsibilities', [])) or 'None recorded.'}",
        "",
        "Write user stories that the context above actually supports.",
    ]
    return (
        "\n".join(scope_lines)
        + "\n\nDOCUMENT CONTEXT (the only material available to you):\n----\n"
        + format_block_context(chunks)
        + "\n----"
    )


def _normalize_role(role: str) -> str:
    """Strip the 'As a ...' wrapper the model sometimes keeps."""
    r = (role or "").strip().rstrip(".")
    lowered = r.lower()
    for prefix in ("as a ", "as an ", "as the ", "as "):
        if lowered.startswith(prefix):
            r = r[len(prefix):].strip()
            lowered = r.lower()
    return r


def _clean_goal(goal: str) -> str:
    g = (goal or "").strip()
    lowered = g.lower()
    for prefix in ("i want to ", "i want ", "so that i can ", "so that "):
        if lowered.startswith(prefix):
            g = g[len(prefix):].strip()
            break
    return g.rstrip(".")


def _clean_benefit(benefit: str) -> str:
    b = (benefit or "").strip()
    lowered = b.lower()
    for prefix in ("so that ", "in order to ", "so as to "):
        if lowered.startswith(prefix):
            b = b[len(prefix):].strip()
            break
    return b.rstrip(".")


def _pick_source(story: UserStory, evidence: list[EvidenceItem], allowed: set[str]) -> str:
    """Attribute a story to a document that is actually in this project."""
    named = (story.source_document or "").strip()
    if named:
        for ev in evidence:
            if ev.document and (ev.document.lower() in named.lower() or named.lower() in ev.document.lower()):
                return ev.document
    return evidence[0].document if evidence else ""


def _is_grounded(story: UserStory, haystack: str) -> bool:
    """Cheap deterministic grounding filter: the goal's distinctive words must
    actually appear in the retrieved context. This is what stops the model from
    smuggling in a requirement of its own invention."""
    words = [w for w in story.goal.lower().split() if len(w) > 3][:6]
    if not words:
        return False
    hits = sum(1 for w in words if w in haystack)
    return hits >= max(1, len(words) // 2)


def generate_user_stories(db: Session, project: Project) -> dict:
    """Generate grounded user stories. Never raises; failures are reported."""
    chunks = retrieve_user_story_context(project.id)
    scope = scope_facts(db, project.id)
    evidence = evidence_from_chunks(chunks)

    result: dict = {
        "doc_type": "user_stories",
        "title": "Project User Stories",
        "project": project.name,
        "stories": [],
        "total": 0,
        "insufficient_evidence": False,
        "evidence": [e.model_dump() for e in evidence],
        "validation": validation.Validator().finish().to_dict(),
        "validation_notes": [],
        "provider": "",
        "model": "",
        "error": None,
    }

    if not chunks:
        result["insufficient_evidence"] = True
        result["reason"] = INSUFFICIENT_EVIDENCE
        return result

    haystack = " ".join(c.get("text", "") for c in chunks).lower()
    prompt = _build_prompt(project, chunks, scope)

    try:
        manager = get_provider_manager()
        text, provider, model = manager.generate(
            prompt, system=USER_STORY_SYSTEM, temperature=0.1, json_mode=True
        )
        result["provider"] = provider
        result["model"] = model
    except AIProviderError as exc:
        # Provider outage must not silently become "no requirements exist".
        result["error"] = str(exc)[:2000]
        result["provider_error"] = True
        return result

    parsed = parse_and_validate(text, UserStoryResult)
    if parsed is None:
        # One deterministic retry, matching the Milestone 2 agent behaviour.
        try:
            text, provider, model = manager.generate(
                prompt, system=USER_STORY_SYSTEM, temperature=0.0, json_mode=True
            )
            parsed = parse_and_validate(text, UserStoryResult)
            result["provider"] = provider
            result["model"] = model
        except AIProviderError:
            parsed = None

    if parsed is None:
        result["error"] = result["error"] or "Model returned unusable structured output."
        return result

    allowed = {e.document for e in evidence if e.document}
    haystack_raw = " ".join(c.get("text", "") for c in chunks)
    grounding = validation.Grounding(
        document_text=haystack_raw,
        structured_text=" ".join(
            [project.name or "", project.objective or "", project.description or ""]
            + [str(f) for f in scope.values() if isinstance(f, (str, list))]
        ),
    )

    stories: list[dict] = []
    validators: list[validation.Validator] = []
    seen: set[str] = set()
    for raw in parsed.user_stories:
        story = UserStory(
            title=(raw.title or "").strip(),
            role=_normalize_role(raw.role),
            goal=_clean_goal(raw.goal),
            benefit=_clean_benefit(raw.benefit),
            acceptance_criteria=[c.strip() for c in raw.acceptance_criteria if c and c.strip()],
            priority=raw.priority,
        )
        if not story.is_substantive():
            continue
        if not _is_grounded(story, haystack):
            # Ungrounded -> dropped. We never publish a requirement we cannot
            # trace to a retrieved chunk.
            continue
        key = f"{story.role.lower()}|{story.goal.lower()}"
        if key in seen:
            continue
        seen.add(key)

        source_document = _pick_source(raw, evidence, allowed)
        candidate = {
            "id": f"US-{len(stories) + 1:03d}",
            "title": story.title or story.goal,
            "as_a": story.role,
            "i_want": story.goal,
            "so_that": story.benefit,
            "role": story.role,
            "goal": story.goal,
            "benefit": story.benefit,
            "acceptance_criteria": story.acceptance_criteria,
            "priority": story.priority.title(),
            "status": "",
            "related_requirement": "",
            "source_document": source_document,
            "source_evidence": (raw.evidence or "").strip(),
        }
        # Correction happens after the story is accepted, so an unsupported field
        # is replaced by the explicit placeholder rather than removing the story.
        candidate, validator = validation.validate_story(
            candidate,
            grounding,
            source_document=source_document,
            field_name_prefix=f"{candidate['id']}.",
        )
        stories.append(candidate)
        validators.append(validator)
        if len(stories) >= MAX_STORIES:
            break

    combined = validation.merge_validators(validators)
    summary = combined.finish()

    result["stories"] = stories
    result["total"] = len(stories)
    result["validation"] = summary.to_dict()
    result["validation_notes"] = combined.notes
    result["insufficient_evidence"] = not stories
    if not stories:
        result["reason"] = INSUFFICIENT_EVIDENCE
    return result


__all__ = ["generate_user_stories", "USER_STORY_SYSTEM", "MAX_STORIES"]
