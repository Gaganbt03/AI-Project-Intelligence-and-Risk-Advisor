"""Automatic post-upload project analysis.

The product rule under test:

    upload a document -> the whole project intelligence pipeline runs by itself

These tests cover the guarantees that rule depends on: the pipeline actually
runs, it is idempotent, it never invents data, it keeps the assistant
user-driven, and the team-oriented presentation is gone from the API.

No live AI provider is contacted. ``tests/fake_ai.py`` replaces only the two
outbound seams — ``ProviderManager.generate`` and the embedding provider — with
deterministic stand-ins, so the real orchestrator, health scorer, documentation
generator and due-date resolver all still run. No production code is changed.
"""

from __future__ import annotations

import pytest

from app.models import Blocker, Project, ProjectDocument, Risk, Task
from app.models_m3 import (
    AssistantConversation,
    AssistantMessage,
    GeneratedDocument,
    HealthSnapshot,
    ProjectPipelineRun,
)

SPEC = """Project Due Date: 2026-12-31

Project goal: deliver the Smart Campus IoT platform.

Scope:
- sensor gateway firmware
- mobile application

Risks:
- The MQTT gateway may be unreliable under load. No owner is stated here.
- Sensor battery life may be insufficient. Assigned to Priya. Deadline 2026-11-20.

Blockers:
- Cloud budget approval is outstanding.

Action items:
- Priya must finalise the sensor datasheet by 2026-10-15.
"""


# --------------------------------------------------------------------------- #
# offline AI — autouse so no test in this module can reach a real provider
# --------------------------------------------------------------------------- #


@pytest.fixture(autouse=True)
def offline_ai(monkeypatch):
    """Swap the provider and embedding seams for deterministic local fakes.

    Autouse rather than per-test: a pipeline test that silently fell back to a
    live provider would be slow and flaky, which is exactly the failure mode
    this fixture exists to prevent.
    """
    from app.services.ai.providers import ProviderManager

    # pytest puts tests/ on sys.path (no package __init__), so this resolves
    # locally without touching how the rest of the suite imports app modules.
    import fake_ai

    calls: list[str] = []

    def recording_generate(self, prompt, system=None, temperature=None, json_mode=False):
        calls.append(prompt[:60])
        return fake_ai.fake_generate(
            self, prompt, system=system, temperature=temperature, json_mode=json_mode
        )

    monkeypatch.setattr(ProviderManager, "generate", recording_generate)

    # Both modules bind the lookup by name at import time, so patch each site.
    embedder = fake_ai.FakeEmbeddingProvider()
    monkeypatch.setattr("app.services.rag.get_embedding_provider", lambda: embedder)
    monkeypatch.setattr("app.services.documents.get_embedding_provider", lambda: embedder)
    monkeypatch.setattr("app.services.embeddings.get_embedding_provider", lambda: embedder)

    yield calls


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #



def _new_project(client, headers, name, **body):
    payload = {"name": name, **body}
    r = client.post("/api/projects", headers=headers, json=payload)
    assert r.status_code in (200, 201), r.text
    return r.json()


def _upload(client, headers, project_id, text=SPEC, filename="spec.txt"):
    r = client.post(
        f"/api/documents/upload?project_id={project_id}",
        headers=headers,
        files={"file": (filename, text.encode(), "text/plain")},
    )
    assert r.status_code in (200, 201), r.text
    return r.json()


def _pipeline_db():
    from app.database import SessionLocal

    return SessionLocal()


def _status(client, headers, project_id):
    r = client.get(f"/api/projects/{project_id}/analysis/status", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def _assistant_counts():
    db = _pipeline_db()
    try:
        return (
            db.query(AssistantConversation).count(),
            db.query(AssistantMessage).count(),
        )
    finally:
        db.close()


# --------------------------------------------------------------------------- #
# the pipeline runs automatically
# --------------------------------------------------------------------------- #


def test_upload_triggers_the_whole_pipeline(client, admin_headers):
    """No manual button: uploading must produce every analysis output."""
    project = _new_project(client, admin_headers, "Auto Pipeline Project")
    _upload(client, admin_headers, project["id"])

    status = _status(client, admin_headers, project["id"])

    assert status["status"] in ("Completed", "CompletedWithWarnings")
    assert len(status["stages"]) == 9

    done = {s["key"] for s in status["stages"] if s["status"] == "done"}
    assert done >= {
        "document_processed",
        "information_extracted",
        "tasks_analyzed",
        "risks_analyzed",
        "blockers_analyzed",
        "forecast_calculated",
        "health_calculated",
        "insights_prepared",
        "documents_ready",
    }

    counts = status["counts"]
    assert counts["documents_processed"] >= 1
    assert counts["risks"] >= 1
    assert counts["blockers"] >= 1
    assert counts["generated_documents"] == 3
    assert counts["health_snapshots"] >= 1

    assert status["assistant_ready"] is True


def test_generated_documents_exist_after_upload(client, admin_headers):
    project = _new_project(client, admin_headers, "Auto Docs Project")
    _upload(client, admin_headers, project["id"])

    r = client.get(f"/api/projects/{project['id']}/documents", headers=admin_headers)
    assert r.status_code == 200, r.text
    types = {d["doc_type"] for d in r.json()["documents"]}
    assert types == {"user_stories", "risk_register", "action_items"}


def test_analysis_run_endpoint_re_analyses_on_demand(client, admin_headers):
    project = _new_project(client, admin_headers, "Manual Rerun Project")
    _upload(client, admin_headers, project["id"])

    r = client.post(f"/api/projects/{project['id']}/analysis/run", headers=admin_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] in ("Completed", "CompletedWithWarnings")
    assert body["trigger"] == "manual"


# --------------------------------------------------------------------------- #
# idempotency
# --------------------------------------------------------------------------- #


def test_re_running_the_pipeline_does_not_duplicate_records(client, admin_headers):
    project = _new_project(client, admin_headers, "Idempotent Project")
    _upload(client, admin_headers, project["id"])
    pid = project["id"]

    def snapshot():
        db = _pipeline_db()
        try:
            return {
                "risks": db.query(Risk).filter(Risk.project_id == pid).count(),
                "tasks": db.query(Task).filter(Task.project_id == pid).count(),
                "blockers": db.query(Blocker).filter(Blocker.project_id == pid).count(),
                "generated": db.query(GeneratedDocument)
                .filter(GeneratedDocument.project_id == pid)
                .count(),
            }
        finally:
            db.close()

    before = snapshot()
    for _ in range(2):
        r = client.post(f"/api/projects/{pid}/analysis/run", headers=admin_headers)
        assert r.status_code == 200, r.text
    after = snapshot()

    assert after["generated"] == before["generated"] == 3
    assert after["risks"] == before["risks"]
    assert after["tasks"] == before["tasks"]
    assert after["blockers"] == before["blockers"]


def test_reprocessing_a_document_reuses_the_same_generated_rows(client, admin_headers):
    project = _new_project(client, admin_headers, "Reprocess Project")
    doc = _upload(client, admin_headers, project["id"])
    pid = project["id"]

    def generated_ids():
        db = _pipeline_db()
        try:
            return sorted(
                d.id
                for d in db.query(GeneratedDocument)
                .filter(GeneratedDocument.project_id == pid)
                .all()
            )
        finally:
            db.close()

    before = generated_ids()
    r = client.post(f"/api/documents/{doc['id']}/reprocess", headers=admin_headers)
    assert r.status_code in (200, 201), r.text

    assert generated_ids() == before


def test_a_pipeline_run_is_recorded_for_audit(client, admin_headers):
    project = _new_project(client, admin_headers, "Run Audit Project")
    _upload(client, admin_headers, project["id"])

    db = _pipeline_db()
    try:
        run = (
            db.query(ProjectPipelineRun)
            .filter(ProjectPipelineRun.project_id == project["id"])
            .order_by(ProjectPipelineRun.id.desc())
            .first()
        )
        assert run is not None
        assert run.status in ("Completed", "CompletedWithWarnings")
        assert run.trigger in ("document_upload", "manual")
        assert run.finished_at is not None
        assert len(run.stages) == 9
        assert {s["status"] for s in run.stages} <= {
            "pending", "running", "done", "skipped", "failed",
        }
    finally:
        db.close()


def test_status_reports_a_finished_run(client, admin_headers):
    """The polled payload must show every stage settled and the run closed out."""
    project = _new_project(client, admin_headers, "Progress Report Project")
    _upload(client, admin_headers, project["id"])

    status = _status(client, admin_headers, project["id"])

    assert status["running"] is False
    assert status["finished_at"] is not None
    # current_stage is cleared when the run closes: it names the stage *in
    # flight*, so a finished run has none.
    assert status["current_stage"] == ""
    assert status["trigger"] in ("document_upload", "manual")
    assert all(s["status"] != "pending" for s in status["stages"])
    assert all(s["status"] != "running" for s in status["stages"])
    # The frontend derives the percentage from this list, so it must be stable.
    assert len(status["stages"]) == 9
    assert status["stages"][-1]["key"] == "documents_ready"
    assert status["stages"][-1]["status"] == "done"


def test_status_before_anything_has_run_is_honest(client, admin_headers):
    project = _new_project(client, admin_headers, "Never Analysed Project")

    status = _status(client, admin_headers, project["id"])

    assert status["status"] == "Not started"
    assert status["running"] is False
    assert status["finished_at"] is None
    assert status["counts"]["documents_processed"] == 0
    assert status["assistant_ready"] is False
    assert status["due_date"]["effective_date"] == "Not specified in project data."
    # The only stage that may be skipped is the one with nothing to work on.
    by_key = {s["key"]: s for s in status["stages"]}
    assert by_key["document_processed"]["status"] == "skipped"



# --------------------------------------------------------------------------- #
# due date precedence — document beats the project form, never invented
# --------------------------------------------------------------------------- #


def test_document_due_date_overrides_the_project_record(client, admin_headers):
    project = _new_project(
        client, admin_headers, "Due Date Conflict", expected_end_date="2026-01-15"
    )
    _upload(client, admin_headers, project["id"])

    due = _status(client, admin_headers, project["id"])["due_date"]

    assert due["effective_date"] == "2026-12-31"
    assert due["document_date"] == "2026-12-31"
    assert due["admin_date"] == "2026-01-15"
    assert due["source"] == "Document"
    assert due["conflict"] is True
    assert "2026-12-31" in due["evidence"]


def test_project_record_due_date_is_used_when_documents_are_silent(client, admin_headers):
    project = _new_project(
        client, admin_headers, "Due Date Admin Only", expected_end_date="2027-03-01"
    )
    _upload(
        client,
        admin_headers,
        project["id"],
        text="Project goal: build a reporting tool.\nScope:\n- charts\n",
        filename="quiet.txt",
    )

    due = _status(client, admin_headers, project["id"])["due_date"]

    assert due["effective_date"] == "2027-03-01"
    assert due["source"] == "Admin"
    assert due["conflict"] is False


def test_missing_due_date_stays_not_specified(client, admin_headers):
    project = _new_project(client, admin_headers, "Due Date Missing")
    _upload(
        client,
        admin_headers,
        project["id"],
        text="Project goal: tidy the backlog.\nScope:\n- clean up tickets\n",
        filename="nodate.txt",
    )

    due = _status(client, admin_headers, project["id"])["due_date"]

    assert due["effective_date"] == "Not specified in project data."
    assert due["source"] == "Not specified"


def test_generated_documents_carry_the_resolved_due_date(client, admin_headers):
    project = _new_project(
        client, admin_headers, "Due Date In Docs", expected_end_date="2026-01-15"
    )
    _upload(client, admin_headers, project["id"])

    r = client.get(f"/api/projects/{project['id']}/documents", headers=admin_headers)
    doc_id = r.json()["documents"][0]["id"]
    full = client.get(
        f"/api/projects/{project['id']}/documents/{doc_id}", headers=admin_headers
    ).json()

    content = full["content"]
    assert "PROJECT " in content.splitlines()[0]
    assert "Project Due Date:** 2026-12-31" in content
    assert "source: Document" in content

    payload_due = full["payload"]["project_due_date"]
    assert payload_due["effective_date"] == "2026-12-31"
    assert payload_due["conflict"] is True


# --------------------------------------------------------------------------- #
# no fabrication
# --------------------------------------------------------------------------- #


def test_health_snapshot_is_recorded_with_a_score(client, admin_headers):
    project = _new_project(client, admin_headers, "Health Snapshot Project")
    _upload(client, admin_headers, project["id"])

    db = _pipeline_db()
    try:
        snap = (
            db.query(HealthSnapshot)
            .filter(HealthSnapshot.project_id == project["id"])
            .order_by(HealthSnapshot.id.desc())
            .first()
        )
        assert snap is not None
        assert 0 <= snap.overall_score <= 100
    finally:
        db.close()


def test_ai_detected_records_keep_their_evidence(client, admin_headers):
    """AI-detected risks must cite what they were derived from.

    ``risks`` has no owner column at all, so the "never invent a field" rule is
    enforced here structurally: a risk cannot exist without evidence naming the
    document it came from.
    """
    project = _new_project(client, admin_headers, "Evidence Preserved")
    _upload(client, admin_headers, project["id"])

    db = _pipeline_db()
    try:
        risks = db.query(Risk).filter(Risk.project_id == project["id"]).all()
        assert risks, "the document states risks, so some should be recorded"
        for risk in risks:
            assert risk.evidence and risk.evidence.strip(), (
                f"risk {risk.id!r} was recorded without evidence"
            )
            assert risk.source_document_id is not None, (
                f"risk {risk.id!r} is not linked to the document it came from"
            )
    finally:
        db.close()


# --------------------------------------------------------------------------- #
# the assistant stays user-driven
# --------------------------------------------------------------------------- #


def test_upload_never_creates_an_assistant_conversation(client, admin_headers):
    before = _assistant_counts()

    project = _new_project(client, admin_headers, "Assistant Silence Project")
    _upload(client, admin_headers, project["id"])
    client.post(f"/api/projects/{project['id']}/analysis/run", headers=admin_headers)

    assert _assistant_counts() == before


def test_assistant_still_works_when_the_user_asks(client, admin_headers):
    """The pipeline must not have broken the assistant; it stays usable."""
    project = _new_project(client, admin_headers, "Assistant Still Works")
    _upload(client, admin_headers, project["id"])

    r = client.post(
        f"/api/projects/{project['id']}/assistant/ask",
        headers=admin_headers,
        json={"question": "What is this project about?"},
    )
    assert r.status_code in (200, 201), r.text
    body = r.json()
    assert body.get("answer") or body.get("response")


# --------------------------------------------------------------------------- #
# team presentation removed, data preserved
# --------------------------------------------------------------------------- #


def test_dashboard_no_longer_reports_team_members(client, admin_headers):
    r = client.get("/api/dashboard", headers=admin_headers)
    assert r.status_code == 200, r.text
    assert "team_members" not in r.json()["summary"]


def test_non_admin_users_keep_their_normal_access(client, employee_headers):
    """Removing the team UI must not change who can read projects."""
    assert client.get("/api/dashboard", headers=employee_headers).status_code == 200
    assert client.get("/api/projects", headers=employee_headers).status_code == 200


@pytest.mark.parametrize(
    "method,path",
    [("get", "/api/projects/1/analysis/status"), ("post", "/api/projects/1/analysis/run")],
)
def test_analysis_endpoints_require_authentication(client, method, path):
    r = getattr(client, method)(path)
    assert r.status_code == 401

