"""Step 5 — grounding of generated documents, reports and assistant answers.

These tests pin the behaviour that generated output must not invent project
facts: missing fields stay ``NOT_SPECIFIED`` (never defaulted to "Medium" /
"Open"), deterministic builders never call an LLM, risk scores are only
computed from recorded levels, story citations point at the document the
requirement was actually read from, and the assistant is not "grounded" by
conversation history alone.

They run against the session-isolated test DB; ``data/app.db`` is never opened.
"""

from __future__ import annotations

import pytest

from app.database import SessionLocal
from app.models import Project, Risk
from app.services.milestone3.common import NOT_SPECIFIED
from app.services.milestone3.documentation.action_items import build_action_items
from app.services.milestone3.documentation.renderer import render_user_stories
from app.services.milestone3.documentation.risk_register import build_risk_register
from app.services.milestone3.documentation.user_stories import _pick_source
from app.services.milestone3.documentation.schemas import EvidenceItem, UserStory


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _new_project(name: str = "Grounding Project") -> int:
    with SessionLocal() as db:
        project = Project(name=name, objective="", priority="Medium", status="Active")
        db.add(project)
        db.commit()
        db.refresh(project)
        return project.id


def _project_with_risks() -> int:
    pid = _new_project("Risk Grounding Project")
    with SessionLocal() as db:
        db.add_all(
            [
                Risk(project_id=pid, title="Levels not recorded",
                     severity="", probability="", impact="", status=""),
                Risk(project_id=pid, title="Complete levels",
                     severity="High", probability="High", impact="High", status="Open"),
            ]
        )
        db.commit()
    return pid


def _register(pid: int) -> dict:
    with SessionLocal() as db:
        project = db.query(Project).filter(Project.id == pid).first()
        return build_risk_register(db, project)


# --------------------------------------------------------------------------- #
# Risk register — missing fields must never be defaulted
# --------------------------------------------------------------------------- #


def test_risk_register_reports_missing_levels_instead_of_defaults():
    entry = next(
        e for e in _register(_project_with_risks())["entries"]
        if e["title"] == "Levels not recorded"
    )
    assert entry["probability"] == NOT_SPECIFIED
    assert entry["impact"] == NOT_SPECIFIED
    assert entry["severity"] == NOT_SPECIFIED
    assert entry["status"] == NOT_SPECIFIED
    assert entry["probability_level"] == ""


def test_risk_score_is_absent_when_levels_are_missing():
    entry = next(
        e for e in _register(_project_with_risks())["entries"]
        if e["title"] == "Levels not recorded"
    )
    assert entry["risk_score"] == NOT_SPECIFIED
    assert entry["severity_band"] == NOT_SPECIFIED
    assert entry["risk_score_value"] == 0
    assert entry["field_verdicts"]["probability"] == "MISSING"
    assert entry["field_verdicts"]["status"] == "MISSING"


def test_risk_score_is_computed_when_all_levels_are_recorded():
    entry = next(
        e for e in _register(_project_with_risks())["entries"]
        if e["title"] == "Complete levels"
    )
    assert 1 <= entry["risk_score"] <= 25
    assert entry["severity_band"] != NOT_SPECIFIED


# --------------------------------------------------------------------------- #
# Renderers — no invented defaults
# --------------------------------------------------------------------------- #


def test_user_story_renderer_never_defaults_priority():
    data = {
        "project": "P",
        "stories": [
            {
                "id": "US-001",
                "title": "Do a thing",
                "role": "Admin",
                "goal": "do a thing",
                "benefit": "",
                "priority": NOT_SPECIFIED,
                "status": NOT_SPECIFIED,
                "acceptance_criteria": [],
                "related_requirement": NOT_SPECIFIED,
                "source_document": NOT_SPECIFIED,
                "source_evidence": NOT_SPECIFIED,
            }
        ],
    }
    out = render_user_stories(data)
    assert f"- **Priority:** {NOT_SPECIFIED}" in out
    assert "Priority:** Medium" not in out


# --------------------------------------------------------------------------- #
# Deterministic builders must not call an LLM
# --------------------------------------------------------------------------- #


def test_documentation_builders_never_call_the_ai(monkeypatch):
    import app.services.ai.providers as providers

    def _boom():  # pragma: no cover - only runs on regression
        raise AssertionError("deterministic documentation must not call the AI provider")

    monkeypatch.setattr(providers, "get_provider_manager", _boom)
    pid = _project_with_risks()
    with SessionLocal() as db:
        project = db.query(Project).filter(Project.id == pid).first()
        build_risk_register(db, project)
        build_action_items(db, project)


# --------------------------------------------------------------------------- #
# Story citation attribution
# --------------------------------------------------------------------------- #


def test_story_source_is_the_document_that_contains_the_requirement():
    chunks = [
        {"text": "The system shall authenticate users with email and password.",
         "original_name": "auth.docx"},
        {"text": "The dashboard shall display project health and risks.",
         "original_name": "dash.docx"},
    ]
    evidence = [EvidenceItem(document="auth.docx"), EvidenceItem(document="dash.docx")]
    story = UserStory(role="Admin", goal="authenticate users with email and password")
    assert _pick_source(story, chunks, evidence) == "auth.docx"


def test_story_source_falls_back_to_evidence_when_nothing_matches():
    chunks = [{"text": "unrelated content here", "original_name": "x.docx"}]
    evidence = [EvidenceItem(document="auth.docx")]
    story = UserStory(role="Admin", goal="zzzzz qqqqq")
    assert _pick_source(story, chunks, evidence) == "auth.docx"


# --------------------------------------------------------------------------- #
# RAG threshold must stay active in production
# --------------------------------------------------------------------------- #


def test_rag_similarity_default_is_active_in_production():
    from app.config import Settings

    default = Settings.model_fields["RAG_MIN_SIMILARITY"].default
    assert default > 0.0


# --------------------------------------------------------------------------- #
# M3 assistant — history alone must not ground an answer
# --------------------------------------------------------------------------- #


def _mock_provider(monkeypatch, text: str):
    import app.services.milestone3.assistant.service as svc

    class _Manager:
        def generate(self, *args, **kwargs):
            return text, "stub", "stub-model"

    monkeypatch.setattr(svc, "retrieve_chunks", lambda *a, **k: [])
    monkeypatch.setattr(svc, "get_provider_manager", lambda: _Manager())


def test_history_alone_does_not_ground_an_answer(fresh_client, admin_headers, monkeypatch):
    pid = fresh_client.get("/api/session/current").json()["project_id"]
    _mock_provider(monkeypatch, "The site will launch in March.")

    conv = fresh_client.post(
        f"/api/projects/{pid}/assistant/conversations",
        headers=admin_headers,
        json={"title": "t"},
    ).json()

    fresh_client.post(
        f"/api/projects/{pid}/assistant/ask",
        headers=admin_headers,
        json={"question": "Tell me everything.", "conversation_id": conv["id"]},
    )

    r = fresh_client.post(
        f"/api/projects/{pid}/assistant/ask",
        headers=admin_headers,
        json={"question": "Anything else?", "conversation_id": conv["id"]},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["grounded"] is False
    assert body["used_conversation_context"] is True


def test_structured_evidence_grounds_without_documents(fresh_client, admin_headers, monkeypatch):
    pid = fresh_client.get("/api/session/current").json()["project_id"]
    with SessionLocal() as db:
        db.add(Risk(project_id=pid, title="A recorded risk", severity="High",
                    probability="High", impact="High", status="Open"))
        db.commit()
    _mock_provider(monkeypatch, "One risk is recorded.")

    r = fresh_client.post(
        f"/api/projects/{pid}/assistant/ask",
        headers=admin_headers,
        json={"question": "How many risks are recorded?", "persist": False},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["grounded"] is True
    assert body["used_structured_data"] is True
    assert body["retrieved_count"] == 0


# --------------------------------------------------------------------------- #
# M1 assistant — a refusal is never reported as grounded
# --------------------------------------------------------------------------- #


def test_m1_assistant_refusal_is_not_grounded(monkeypatch):
    import app.services.assistant as m1

    monkeypatch.setattr(m1, "retrieve_chunks", lambda *a, **k: [{"score": 0.9, "text": "x"}])

    class _Manager:
        def generate(self, *args, **kwargs):
            return m1.NOT_FOUND_MSG, "stub", "stub-model"

    monkeypatch.setattr(m1, "get_provider_manager", lambda: _Manager())
    result = m1.ask(project_id=1, question="What is the budget?")
    assert result["grounded"] is False
    assert result["answer"] == m1.NOT_FOUND_MSG


def test_m1_assistant_empty_reply_is_not_grounded(monkeypatch):
    import app.services.assistant as m1

    monkeypatch.setattr(m1, "retrieve_chunks", lambda *a, **k: [{"score": 0.9, "text": "x"}])

    class _Manager:
        def generate(self, *args, **kwargs):
            return "", "stub", "stub-model"

    monkeypatch.setattr(m1, "get_provider_manager", lambda: _Manager())
    result = m1.ask(project_id=1, question="What is the budget?")
    assert result["grounded"] is False
