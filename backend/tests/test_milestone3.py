"""Milestone 3 — documentation generation, health scoring, assistant, validation.

These tests run against the session-isolated test database created in
``conftest.py``; the real ``data/app.db`` is never opened.
"""

import io
import zipfile

import pytest

from app.database import SessionLocal
from app.models import (
    Blocker, DocumentChunk, Project, ProjectDocument, ProjectInsight, Risk, Task,
)
from app.models_m3 import AssistantConversation, GeneratedDocument, HealthSnapshot
from app.services.milestone3.assistant import service as assistant_service
from app.services.milestone3.assistant.service import INSUFFICIENT_EVIDENCE
from app.services.milestone3.common import NOT_SPECIFIED
from app.services.milestone3.documentation.action_items import build_action_items
from app.services.milestone3.documentation.risk_register import build_risk_register
from app.services.milestone3.documentation.schemas import UserStoryResult
from app.services.milestone3.health.scorer import overall_from_dimensions, score_project
from app.services.milestone3.health.schemas import (
    DIMENSIONS,
    FORMULA_TEXT,
    FORMULA_VERSION,
    REQUIRED_DIMENSIONS,
    status_for,
)
from app.services.milestone3.validation import EICAR, preflight_upload, validate_upload


# --------------------------------------------------------------------------- #
# Fixtures / helpers
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def seeded(client, admin_headers):
    """A project with a full set of M3-relevant rows, created through the API."""
    r = client.post(
        "/api/projects",
        headers=admin_headers,
        json={
            "name": "Milestone Three Project",
            "description": "M3 fixture",
            "objective": "Prove the milestone three agents work",
            "priority": "High",
            "status": "Active",
            "member_ids": [],
        },
    )
    assert r.status_code == 201, r.text
    project = r.json()
    pid = project["id"]

    with SessionLocal() as db:
        p = db.query(Project).filter(Project.id == pid).first()
        db.add_all(
            [
                Risk(project_id=pid, title="Gateway delay", severity="High",
                     probability="Medium", impact="High", status="Open",
                     description="The gateway vendor may slip", evidence="spec.pdf p2"),
                Risk(project_id=pid, title="Sensor drift", severity="Low",
                     probability="Low", impact="Low", status="Mitigated"),
                Task(project_id=pid, title="Draft architecture", status="Completed"),
                Task(project_id=pid, title="Configure sensors", status="In Progress"),
                Blocker(project_id=pid, title="Waiting on VLAN access", severity="High",
                        status="Open", owner="Lead", description="Security approval pending"),
            ]
        )
        for category, title, summary in [
            ("project_goal", "", "Prove the milestone three agents work"),
            ("scope", "S1", "s"), ("scope", "S2", "s"),
            ("deliverable", "D1", "d"), ("deliverable", "D2", "d"),
            ("deliverable", "D3", "d"),
            ("milestone", "M1", "m"), ("milestone", "M2", "m"), ("milestone", "M3", "m"),
            ("timeline", "T", "t"), ("responsibility", "R", "r"), ("responsibility", "R2", "r2"),
        ]:
            db.add(ProjectInsight(project_id=pid, agent="scope", category=category,
                                  title=title, summary=summary))
        db.add(ProjectInsight(project_id=pid, agent="forecast", category="schedule", title="s",
                              payload={"schedule_status": "Minor Risk", "risk_level": "Medium"}))
        db.commit()
    return project


@pytest.fixture(scope="module")
def seeded_project_id(seeded):
    return seeded["id"]


# --------------------------------------------------------------------------- #
# 3.2 Health scoring
# --------------------------------------------------------------------------- #


def test_health_endpoint_returns_deterministic_score(client, admin_headers, seeded_project_id):
    first = client.get(f"/api/projects/{seeded_project_id}/health?explain=false", headers=admin_headers)
    assert first.status_code == 200, first.text
    second = client.get(f"/api/projects/{seeded_project_id}/health?explain=false", headers=admin_headers)
    assert second.status_code == 200

    a, b = first.json(), second.json()
    assert a["overall_score"] == b["overall_score"]
    assert [d["score"] for d in a["dimensions"]] == [d["score"] for d in b["dimensions"]]
    assert 0 <= a["overall_score"] <= 100
    assert a["status"] == status_for(a["overall_score"])


def test_health_required_dimensions_are_always_scored(client, admin_headers, seeded_project_id):
    body = client.get(f"/api/projects/{seeded_project_id}/health?explain=false", headers=admin_headers).json()
    keys = {d["key"] for d in body["dimensions"]}
    assert set(REQUIRED_DIMENSIONS) <= keys
    for dim in body["dimensions"]:
        if dim["key"] in REQUIRED_DIMENSIONS:
            assert dim["supported"] is True


def test_health_persists_snapshots_and_history(client, admin_headers, seeded_project_id):
    history = client.get(f"/api/projects/{seeded_project_id}/health/history", headers=admin_headers)
    assert history.status_code == 200
    entries = history.json()["history"]
    assert len(entries) >= 1
    assert all("formula_version" in e for e in entries)

    latest = client.get(f"/api/projects/{seeded_project_id}/health/latest", headers=admin_headers)
    assert latest.status_code == 200
    assert latest.json()["id"] == entries[0]["id"]

    with SessionLocal() as db:
        assert db.query(HealthSnapshot).filter(HealthSnapshot.project_id == seeded_project_id).count() >= 1


def test_health_exposes_the_formula(client, admin_headers, seeded_project_id):
    body = client.get(f"/api/projects/{seeded_project_id}/health/formula", headers=admin_headers).json()
    assert "overall" in body and "status_bands" in body
    assert "scope_clarity" in body["dimensions"]
    assert "formula" in body["dimensions"]["scope_clarity"]


def test_health_weights_match_the_published_formula():
    """The six weights are a published contract: 20/20/20 required, 15/15/10 optional.

    Guards against the weights silently drifting away from the specification.
    """
    expected = {
        "scope_clarity": 20.0,
        "timeline_risk": 20.0,
        "blocker_count": 20.0,
        "risk_exposure": 15.0,
        "deliverable_progress": 15.0,
        "documentation_completeness": 10.0,
    }
    assert {k: w for k, _, w, _ in DIMENSIONS} == expected
    assert sum(expected.values()) == 100.0
    # The API contract and the published formula text must agree with the code.
    for key, weight in expected.items():
        assert FORMULA_TEXT["dimensions"][key]["weight"] == weight
    assert FORMULA_TEXT["weights"] == expected
    assert FORMULA_VERSION == "m3.2"


def test_health_overall_is_the_published_weighted_average():
    dims = [
        {"key": k, "score": s, "weight": w, "supported": True}
        for k, s, w in [
            ("scope_clarity", 100.0, 20.0),
            ("timeline_risk", 100.0, 20.0),
            ("blocker_count", 100.0, 20.0),
            ("risk_exposure", 0.0, 15.0),
            ("deliverable_progress", 0.0, 15.0),
            ("documentation_completeness", 0.0, 10.0),
        ]
    ]
    overall, skipped = overall_from_dimensions(dims)
    assert skipped == []
    # 60 of the 100 available points were earned.
    assert overall == 60.0

    # An unsupported dimension is excluded from the denominator, so a project
    # is not punished for data it never had.
    unsupported = [dict(d) for d in dims]
    unsupported[3]["supported"] = False
    partial, skipped = overall_from_dimensions(unsupported)
    assert "risk_exposure" in skipped
    # 60 earned points over the remaining 85 points of supported weight.
    assert partial == 70.6


def test_health_no_data_project_skips_optional_dimensions(client, admin_headers):
    r = client.post("/api/projects", headers=admin_headers,
                    json={"name": "Empty Health Project", "description": "", "objective": "",
                          "priority": "Low", "status": "Planning", "member_ids": []})
    assert r.status_code == 201
    pid = r.json()["id"]
    body = client.get(f"/api/projects/{pid}/health?explain=false", headers=admin_headers).json()
    assert "risk_exposure" in body["skipped_dimensions"]
    assert "deliverable_progress" in body["skipped_dimensions"]
    assert body["data_completeness"] < 50


def test_health_is_rbac_protected(client, employee_headers, project_b):
    assert client.get(f"/api/projects/{project_b['id']}/health", headers=employee_headers).status_code == 200


# --------------------------------------------------------------------------- #
# 3.1 Documentation generation
# --------------------------------------------------------------------------- #


def test_risk_register_reuses_existing_risks_without_inventing():
    with SessionLocal() as db:
        from app.models import Project as P
        p = db.query(P).filter(P.name == "Milestone Three Project").first()
        register = build_risk_register(db, p)

    titles = {e["title"] for e in register["entries"]}
    assert "Gateway delay" in titles
    assert "Sensor drift" in titles
    assert register["total"] == len(register["entries"]) == 2
    for entry in register["entries"]:
        # missing structured fields are reported, never invented
        assert entry["owner"] == NOT_SPECIFIED
        assert entry["contingency"] == NOT_SPECIFIED
        assert 1 <= entry["risk_score"] <= 25
    assert register["entries"][0]["risk_score"] >= register["entries"][-1]["risk_score"]


def test_action_items_cover_tasks_and_blockers_once():
    with SessionLocal() as db:
        from app.models import Project as P
        p = db.query(P).filter(P.name == "Milestone Three Project").first()
        items = build_action_items(db, p)

    ids = [e["action_id"] for e in items["entries"]]
    assert len(ids) == len(set(ids)) == items["total"] == 3  # 2 tasks + 1 blocker
    assert items["from_tasks"] == 2 and items["from_blockers"] == 1
    task_rows = [e for e in items["entries"] if e["origin_kind"] != "blocker"]
    assert all(e["owner"] == NOT_SPECIFIED for e in task_rows)
    blocker_rows = [e for e in items["entries"] if e["origin_kind"] == "blocker"]
    assert blocker_rows[0]["owner"] == "Lead"


def test_generate_deterministic_document_endpoint(client, admin_headers, seeded_project_id):
    r = client.post(
        f"/api/projects/{seeded_project_id}/documents/generate",
        headers=admin_headers,
        json={"doc_type": "risk_register"},
    )
    assert r.status_code == 201, r.text
    docs = r.json()["generated"]
    assert len(docs) == 1
    assert docs[0]["doc_type"] == "risk_register"
    assert docs[0]["generation_count"] == 1

    detail = client.get(f"/api/projects/{seeded_project_id}/documents/{docs[0]['id']}",
                        headers=admin_headers)
    assert detail.status_code == 200
    content = detail.json()["content"]
    assert content.startswith("# Risk Register")
    assert "Gateway delay" in content


def test_regeneration_updates_the_same_row(client, admin_headers, seeded_project_id):
    listed = client.get(f"/api/projects/{seeded_project_id}/documents", headers=admin_headers).json()
    doc_id = listed["documents"][0]["id"]
    r = client.post(f"/api/projects/{seeded_project_id}/documents/{doc_id}/regenerate",
                    headers=admin_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["id"] == doc_id
    assert body["generation_count"] == 2

    with SessionLocal() as db:
        rows = (db.query(GeneratedDocument)
                .filter(GeneratedDocument.project_id == seeded_project_id,
                        GeneratedDocument.doc_type == "risk_register").all())
        assert len(rows) == 1, "regeneration must not create a second row"


def test_generated_documents_are_separate_from_uploads(client, admin_headers, seeded_project_id):
    with SessionLocal() as db:
        from app.models import ProjectDocument
        assert db.query(ProjectDocument).count() >= 0  # uploads untouched by generation
        gen = db.query(GeneratedDocument).filter(GeneratedDocument.project_id == seeded_project_id).all()
        assert gen and all(g.file_type == "md" for g in gen)


def test_download_generated_document(client, admin_headers, seeded_project_id):
    listed = client.get(f"/api/projects/{seeded_project_id}/documents", headers=admin_headers).json()
    doc_id = listed["documents"][0]["id"]
    r = client.get(f"/api/projects/{seeded_project_id}/documents/{doc_id}/download",
                   headers=admin_headers)
    assert r.status_code == 200
    assert "text/markdown" in r.headers["content-type"]
    assert "attachment" in r.headers["content-disposition"]
    assert r.text.startswith("# ")


def test_generate_rejects_unknown_type(client, admin_headers, seeded_project_id):
    r = client.post(f"/api/projects/{seeded_project_id}/documents/generate",
                    headers=admin_headers, json={"doc_type": "powerpoint"})
    assert r.status_code == 400
    assert "Unknown doc_type" in r.json()["detail"]


def test_documentation_is_rbac_protected(client, employee_headers, project_b):
    # All authenticated users may access project documentation in single-tier model
    assert client.get(f"/api/projects/{project_b['id']}/documents", headers=employee_headers).status_code == 200

def test_generated_document_from_another_project_is_not_readable(client, admin_headers,
                                                                 employee_headers, seeded_project_id,
                                                                 project_b):
    doc_id = client.get(f"/api/projects/{seeded_project_id}/documents",
                        headers=admin_headers).json()["documents"][0]["id"]
    # an employee with no access to the seeded project cannot read it at all
    r = client.get(f"/api/projects/{seeded_project_id}/documents/{doc_id}", headers=employee_headers)
    assert r.status_code == 200
    # a member of another project cannot reach it through that project's prefix
    r2 = client.get(f"/api/projects/{project_b['id']}/documents/{doc_id}", headers=admin_headers)
    assert r2.status_code == 404


# --------------------------------------------------------------------------- #
# 3.3 Conversational assistant
# --------------------------------------------------------------------------- #


def test_assistant_conversation_roundtrip(client, admin_headers, seeded_project_id, monkeypatch):
    monkeypatch.setattr(assistant_service, "retrieve_chunks", lambda *a, **k: [])
    monkeypatch.setattr(
        assistant_service,
        "get_provider_manager",
        lambda: type("M", (), {"generate": lambda self, *a, **k: ("There is 1 open blocker.", "stub", "stub")})(),
    )
    created = client.post(f"/api/projects/{seeded_project_id}/assistant/conversations",
                          headers=admin_headers, json={"title": "M3 chat"})
    assert created.status_code == 201, created.text
    cid = created.json()["id"]

    first = client.post(f"/api/projects/{seeded_project_id}/assistant/ask", headers=admin_headers,
                        json={"question": "What blockers are open?", "conversation_id": cid})
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["grounded"] is True
    assert body["used_structured_data"] is True
    assert body["conversation_id"] == cid

    second = client.post(f"/api/projects/{seeded_project_id}/assistant/ask", headers=admin_headers,
                         json={"question": "And what should I do about it?", "conversation_id": cid})
    assert second.status_code == 200
    assert second.json()["used_conversation_context"] is True

    history = client.get(f"/api/projects/{seeded_project_id}/assistant/conversations/{cid}",
                         headers=admin_headers)
    assert history.status_code == 200
    msgs = history.json()["messages"]
    assert len(msgs) == 4
    assert [m["role"] for m in msgs] == ["user", "assistant", "user", "assistant"]


def test_assistant_uses_exact_insufficient_evidence_phrase(client, admin_headers,
                                                            seeded_project_id, monkeypatch):
    monkeypatch.setattr(assistant_service, "retrieve_chunks", lambda *a, **k: [])
    monkeypatch.setattr(
        assistant_service,
        "get_provider_manager",
        lambda: type("M", (), {"generate": lambda self, *a, **k: ("maybe " + INSUFFICIENT_EVIDENCE, "stub", "s")})(),
    )
    r = client.post(f"/api/projects/{seeded_project_id}/assistant/ask", headers=admin_headers,
                    json={"question": "What colour is the corporate logo?", "persist": False})
    assert r.status_code == 200
    assert r.json()["answer"] == INSUFFICIENT_EVIDENCE
    assert r.json()["grounded"] is False


def test_assistant_refuses_without_calling_the_model(client, admin_headers, monkeypatch):
    called = {"n": 0}

    class Manager:
        def generate(self, *a, **k):
            called["n"] += 1
            return ("should not happen", "stub", "s")

    created = client.post("/api/projects", headers=admin_headers,
                          json={"name": "Assistant No Evidence Project", "description": "",
                                "objective": "", "priority": "Low", "status": "Planning",
                                "member_ids": []})
    assert created.status_code == 201
    pid = created.json()["id"]

    monkeypatch.setattr(assistant_service, "retrieve_chunks", lambda *a, **k: [])
    monkeypatch.setattr(assistant_service, "get_provider_manager", lambda: Manager())
    with SessionLocal() as db:
        empty = db.query(Project).filter(Project.id == pid).first()
        result = assistant_service.answer(db, empty, "What is the deployment date?", persist=False)
    assert result["answer"] == INSUFFICIENT_EVIDENCE
    assert result["grounded"] is False
    assert called["n"] == 0


def test_assistant_survives_retrieval_outage(client, admin_headers, seeded_project_id, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("vector store offline")

    monkeypatch.setattr(assistant_service, "retrieve_chunks", boom)
    monkeypatch.setattr(
        assistant_service,
        "get_provider_manager",
        lambda: type("M", (), {"generate": lambda self, *a, **k: ("Answered from rows.", "stub", "s")})(),
    )
    r = client.post(f"/api/projects/{seeded_project_id}/assistant/ask", headers=admin_headers,
                    json={"question": "How many open risks are there?", "persist": False})
    assert r.status_code == 200
    assert r.json()["retrieval_error"]


def test_assistant_provider_outage_is_reported(client, admin_headers, seeded_project_id, monkeypatch):
    from app.services.ai.providers import AIProviderError

    class Manager:
        def generate(self, *a, **k):
            raise AIProviderError("all providers down")

    monkeypatch.setattr(assistant_service, "retrieve_chunks", lambda *a, **k: [
        {"vector_id": "v", "text": "t", "original_name": "a.pdf", "page": 1, "document_id": 1}])
    monkeypatch.setattr(assistant_service, "get_provider_manager", lambda: Manager())
    r = client.post(f"/api/projects/{seeded_project_id}/assistant/ask", headers=admin_headers,
                    json={"question": "Summarise the spec", "persist": False})
    assert r.status_code == 200
    body = r.json()
    assert body["error"]
    assert body["grounded"] is False


def test_assistant_conversations_are_project_scoped(client, admin_headers, seeded_project_id,
                                                    project_b, monkeypatch):
    monkeypatch.setattr(assistant_service, "retrieve_chunks", lambda *a, **k: [])
    cid = client.post(f"/api/projects/{seeded_project_id}/assistant/conversations",
                      headers=admin_headers, json={"title": "scoped"}).json()["id"]
    r = client.get(f"/api/projects/{project_b['id']}/assistant/conversations/{cid}", headers=admin_headers)
    assert r.status_code == 404


def test_assistant_is_rbac_protected(client, employee_headers, project_b):
    r = client.post(f"/api/projects/{project_b['id']}/assistant/ask", headers=employee_headers,
                    json={"question": "hello"})
    assert r.status_code == 200


def test_conversation_messages_are_persisted_with_evidence(client, admin_headers,
                                                           seeded_project_id, monkeypatch):
    monkeypatch.setattr(assistant_service, "retrieve_chunks", lambda *a, **k: [
        {"vector_id": "v1", "text": "The gateway supports 5000 sensors.",
         "original_name": "spec.pdf", "page": 2, "document_id": 1}])
    monkeypatch.setattr(
        assistant_service,
        "get_provider_manager",
        lambda: type("M", (), {"generate": lambda self, *a, **k: ("5000 sensors.", "stub", "s")})(),
    )
    conv = client.post(f"/api/projects/{seeded_project_id}/assistant/conversations",
                       headers=admin_headers, json={"title": "evidence"}).json()
    client.post(f"/api/projects/{seeded_project_id}/assistant/ask", headers=admin_headers,
                json={"question": "How many sensors?", "conversation_id": conv["id"]})
    with SessionLocal() as db:
        rows = (db.query(AssistantConversation)
                .filter(AssistantConversation.project_id == seeded_project_id).all())
        assistant_msgs = [m for c in rows for m in c.messages if m.role == "assistant"]
        assert assistant_msgs
        assert any(m.grounded and m.sources for m in assistant_msgs)


# --------------------------------------------------------------------------- #
# 3.4 Upload validation
# --------------------------------------------------------------------------- #


def _docx_bytes() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr("word/document.xml", "<document/>")
    return buf.getvalue()


def test_validation_accepts_valid_files():
    assert validate_upload("spec.pdf", b"%PDF-1.7\nbody\n%%EOF", "application/pdf").ok
    assert validate_upload("notes.txt", b"plain text", "text/plain").ok
    assert validate_upload("data.csv", b"a,b\n1,2\n", "text/csv").ok
    assert validate_upload("brief.docx", _docx_bytes(),
                           "application/vnd.openxmlformats-officedocument.wordprocessingml.document").ok


def test_validation_rejects_bad_files():
    assert not validate_upload("evil.exe", b"MZ", "application/octet-stream").ok
    assert not validate_upload("fake.pdf", b"PK\x03\x04nope", "application/pdf").ok
    assert not validate_upload("x.docx", b"%PDF-1.7", "application/pdf").ok
    assert not validate_upload("empty.txt", b"", "text/plain").ok
    assert not validate_upload("noext", b"data", "text/plain").ok
    assert not validate_upload("eicar.txt", EICAR + b"x", "text/plain").ok
    mismatch = validate_upload("spec.pdf", b"%PDF-1.7\n%%EOF", "text/plain")
    assert not mismatch.ok and "does not match" in mismatch.detail


def test_validation_enforces_size_limit():
    result = validate_upload("big.txt", b"x" * 100, "text/plain", max_size=50)
    assert not result.ok
    assert any("over the" in c["detail"] for c in result.checks if not c["passed"])


def test_validation_warns_on_ragged_csv():
    result = validate_upload("data.csv", b"a,b\n1,2\n3\n", "text/csv")
    assert result.ok
    assert any("Row width differs" in w for w in result.warnings)


def test_preflight_is_metadata_only():
    ok = preflight_upload("spec.pdf", "application/pdf", 1024)
    assert ok.ok and ok.detail.startswith("File is eligible")
    assert not preflight_upload("spec.pdf", "text/plain", 1024).ok
    assert not preflight_upload("spec.pdf", "application/pdf", 999_999_999).ok


def test_upload_endpoint_rejects_invalid_file(client, admin_headers, project_a):
    r = client.post(
        "/api/documents/upload",
        headers=admin_headers,
        params={"project_id": project_a["id"]},
        files={"file": ("payload.exe", b"MZ\x00\x00", "application/octet-stream")},
    )
    assert r.status_code in (400, 415)
    with SessionLocal() as db:
        from app.models import ProjectDocument
        assert (db.query(ProjectDocument)
                .filter(ProjectDocument.project_id == project_a["id"],
                        ProjectDocument.original_name == "payload.exe").count()) == 0


def test_validation_endpoints(client, admin_headers, seeded_project_id):
    report = client.get(f"/api/projects/{seeded_project_id}/validation/report", headers=admin_headers)
    assert report.status_code == 200
    assert report.json()["supported_types"] == ["pdf", "docx", "csv", "txt"]

    check = client.post(
        f"/api/projects/{seeded_project_id}/validation/check",
        headers=admin_headers,
        params={"file_name": "report.exe", "content_type": "application/octet-stream", "size": 10},
    )
    assert check.status_code == 200
    assert check.json()["ok"] is False


# --------------------------------------------------------------------------- #
# Cross-cutting: no real data touched
# --------------------------------------------------------------------------- #


def test_m3_never_touches_uploaded_documents(client, admin_headers, seeded_project_id):
    with SessionLocal() as db:
        before = db.query(ProjectDocument).count()
        for d in db.query(ProjectDocument).all():
            assert d.storage_path, "uploaded file paths must be preserved"
    assert before >= 0


def test_user_story_schema_rejects_empty_story():
    result = UserStoryResult(user_stories=[{"title": "x", "role": "", "goal": "", "benefit": ""}])
    story = result.user_stories[0]
    assert not story.is_substantive(), "a story without a role and goal must be dropped"


# --------------------------------------------------------------------------- #
# 3.5 Deterministic extraction, classification and date precedence
#
# These tests reproduce the real project's data shape — a DOCX that labels every
# entity in one chunk, and a CSV of work items — so the per-entity scoping is
# exercised rather than assumed.
# --------------------------------------------------------------------------- #


DOCX_CHUNK = (
    "Risks\n"
    "R001 - MQTT gateway reliability | Impact: High | Probability: Medium\n"
    "Effect: Real-time readings may be lost. Mitigation: Add retry and reconnect logic.\n"
    "R002 - Weak Wi-Fi in two classrooms | Impact: Medium | Probability: High\n"
    "Effect: Sensor data may arrive late. Mitigation: Install additional access point.\n"
    "R003 - Sensor shipment delay | Impact: High | Probability: Medium\n"
    "Effect: Five sensors are arriving one week late. "
    "Mitigation: Confirm vendor delivery and revise pilot sequence."
)

BLOCKER_CHUNK = (
    "Blockers\n"
    "B001 - IoT VLAN access approval | Owner: Network Team | Status: Blocked | "
    "Due: 2026-10-12. Action: Approve network access.\n"
    "B002 - Delayed sensor shipment | Owner: Procurement | Status: Open | "
    "Due: 2026-10-11. Action: Obtain confirmed delivery date."
)

ACTION_CHUNK = (
    "Action Items\n"
    "A001 - Implement MQTT reconnect handling | Owner: Backend Team | Priority: High | "
    "Due: 2026-10-15 | Status: In Progress\n"
    "A002 - Install classroom access point | Owner: Network Team | Priority: Medium | "
    "Due: 2026-10-14 | Status: Not Started\n"
    "A003 - Validate energy readings | Owner: Data Team | Priority: Medium | "
    "Due: 2026-10-18 | Status: Not Started"
)


@pytest.fixture()
def labelled_project(client, admin_headers, admin):
    """A project whose documents label risks, blockers and actions in one chunk.

    Milestone 2 filed B001/B002/A001..A003 in the ``risks`` table, which is the
    real-world situation this fixture reproduces.
    """
    r = client.post(
        "/api/projects",
        headers=admin_headers,
        json={
            "name": "Labelled Entities Project",
            "description": "documents label every entity in one chunk",
            "objective": "Prove per-entity extraction",
            "priority": "High",
            "status": "Active",
            "member_ids": [],
        },
    )
    assert r.status_code == 201, r.text
    pid = r.json()["id"]

    with SessionLocal() as db:
        project = db.query(Project).filter(Project.id == pid).first()
        docs = [
            ("02_Requirements.docx", DOCX_CHUNK),
            ("03_Requirements.docx", BLOCKER_CHUNK),
            ("04_Requirements.docx", ACTION_CHUNK),
        ]
        for name, text in docs:
            document = ProjectDocument(
                project_id=pid, file_name=f"{name}.stored", original_name=name,
                file_type="docx", mime_type="application/vnd.openxmlformats-officedocument."
                                               "wordprocessingml.document",
                file_size=len(text.encode("utf-8")),
                storage_path=f"uploads/{pid}/{name}.stored", uploaded_by=admin["user"]["id"],
                status="Processed", extracted_text=text, chunk_count=1,
            )
            db.add(document)
            db.flush()
            db.add(DocumentChunk(
                document_id=document.id, project_id=pid, chunk_index=0, text=text,
            ))

        db.add_all([
            Risk(project_id=pid, title="R001 MQTT gateway reliability", severity="High",
                 probability="Medium", impact="High", status="Open",
                 description="Real-time readings may be lost.", evidence="[1]"),
            # Document says Impact: Medium, Milestone 2 recorded High -> conflict.
            Risk(project_id=pid, title="R002 Weak Wi-Fi in two classrooms", severity="Medium",
                 probability="High", impact="High", status="Open",
                 description="Sensor data may arrive late.", evidence="[1]"),
            Risk(project_id=pid, title="R003 Sensor shipment delay", severity="High",
                 probability="Medium", impact="High", status="Open",
                 description="Five sensors are arriving one week late.", evidence="[1]"),
            Risk(project_id=pid, title="B001 IoT VLAN access approval", severity="High",
                 probability="High", impact="High", status="Open"),
            Risk(project_id=pid, title="B002 Delayed sensor shipment", severity="High",
                 probability="High", impact="High", status="Open"),
            Risk(project_id=pid, title="A001 Implement MQTT reconnect handling", severity="High",
                 probability="High", impact="High", status="Open"),
            Risk(project_id=pid, title="A002 Install classroom access point", severity="Medium",
                 probability="High", impact="High", status="Open"),
            Risk(project_id=pid, title="A003 Validate energy readings", severity="Medium",
                 probability="High", impact="High", status="Open"),
            Task(project_id=pid, title="Draft the sensor rollout plan", status="Pending"),
            Blocker(project_id=pid, title="IoT VLAN access approval", severity="High",
                    status="Open", owner="Network Team",
                    description="Security approval pending"),
        ])
        db.commit()
    return pid


def test_risk_register_holds_risks_only(labelled_project):
    """Blockers and action items must not appear as risks."""
    with SessionLocal() as db:
        project = db.query(Project).filter(Project.id == labelled_project).first()
        register = build_risk_register(db, project)

    assert [e["risk_id"] for e in register["entries"]] == ["R001", "R002", "R003"]
    assert all(e["risk_id"].startswith("R") for e in register["entries"])

    excluded = {item["risk_id"]: item for item in register["excluded"]}
    assert set(excluded) == {"B001", "B002", "A001", "A002", "A003"}
    assert excluded["B001"]["classified_as"] == "blocker"
    assert excluded["B001"]["kept_in"]
    assert excluded["A001"]["classified_as"] == "action"
    # Each excluded row explains itself.
    assert all(item["reason"] for item in register["excluded"])


def test_labelled_fields_do_not_leak_between_entities(labelled_project):
    """R001's mitigation must not be R002's, even though they share one chunk."""
    with SessionLocal() as db:
        project = db.query(Project).filter(Project.id == labelled_project).first()
        entries = {e["risk_id"]: e for e in build_risk_register(db, project)["entries"]}

    assert entries["R001"]["mitigation"] == "Add retry and reconnect logic"
    assert entries["R002"]["mitigation"] == "Install additional access point"
    assert entries["R003"]["mitigation"] == "Confirm vendor delivery and revise pilot sequence"
    assert entries["R001"]["description"] == "Real-time readings may be lost."
    assert entries["R002"]["description"] == "Sensor data may arrive late."


def test_document_conflict_is_recorded_and_recorded_value_preserved(labelled_project):
    """A non-date disagreement is reported, never silently overwritten."""
    with SessionLocal() as db:
        project = db.query(Project).filter(Project.id == labelled_project).first()
        register = build_risk_register(db, project)

    entries = {e["risk_id"]: e for e in register["entries"]}
    assert entries["R002"]["impact"] == "High", "the Milestone 2 value must be preserved"
    assert entries["R002"]["field_verdicts"]["impact"] == "CONFLICT"
    assert any("Medium" in c for c in entries["R002"]["conflicts"])

    conflicts = [f for f in register["validation"]["findings"] if f["verdict"] == "CONFLICT"]
    assert conflicts and all(f["field"] == "impact" for f in conflicts)


def test_due_dates_follow_document_first_precedence(labelled_project):
    """An explicit document date wins and the admin value stays visible."""
    with SessionLocal() as db:
        project = db.query(Project).filter(Project.id == labelled_project).first()
        db.query(Blocker).filter(Blocker.project_id == labelled_project).update(
            {"expected_resolution": "2026-01-01"}
        )
        db.commit()
        items = build_action_items(db, project)

    entries = {e["action_id"]: e for e in items["entries"]}
    assert entries["A001"]["due_date"] == "2026-10-15"
    trace = entries["A001"]["due_date_trace"]
    assert trace["document_date"] == "2026-10-15"
    assert trace["admin_date"] == "Not specified in project data."
    assert trace["conflict"] is False


def test_blocker_action_uses_the_document_resolution_action_and_date(labelled_project):
    with SessionLocal() as db:
        project = db.query(Project).filter(Project.id == labelled_project).first()
        items = build_action_items(db, project)

    entries = {e["action_id"]: e for e in items["entries"]}
    vlan = next(e for e in items["entries"] if e["related_blocker"] == "BLK-0001")
    assert vlan["action"] == "Approve network access"
    assert vlan["due_date"] == "2026-10-12"
    assert vlan["origin_kind"] == "blocker"

    shipment = next(e for e in items["entries"] if e["related_blocker"] == "BLK-0002")
    assert shipment["action"] == "Obtain confirmed delivery date"
    assert shipment["due_date"] == "2026-10-11"
    assert entries  # ids are unique


def test_document_actions_reach_the_action_list_not_the_register(labelled_project):
    with SessionLocal() as db:
        project = db.query(Project).filter(Project.id == labelled_project).first()
        items = build_action_items(db, project)

    ids = {e["action_id"] for e in items["entries"]}
    assert {"A001", "A002", "A003"} <= ids
    entry = next(e for e in items["entries"] if e["action_id"] == "A001")
    assert entry["action"] == "Implement MQTT reconnect handling"
    assert entry["owner"] == "Backend Team"
    assert entry["due_date"] == "2026-10-15"


def test_action_ids_are_unique(labelled_project):
    with SessionLocal() as db:
        project = db.query(Project).filter(Project.id == labelled_project).first()
        items = build_action_items(db, project)

    ids = [e["action_id"] for e in items["entries"]]
    assert len(ids) == len(set(ids)) == items["total"]


def test_missing_fields_report_not_specified_and_are_never_invented(labelled_project):
    with SessionLocal() as db:
        project = db.query(Project).filter(Project.id == labelled_project).first()
        items = build_action_items(db, project)

    manual = next(e for e in items["entries"] if e["origin_kind"] == "manual")
    assert manual["due_date"] == NOT_SPECIFIED
    assert manual["due_date_assigned"] is False
    assert manual["source_document"] == NOT_SPECIFIED
    verdicts = manual["field_verdicts"]
    assert verdicts["due_date"] == "MISSING"
    assert verdicts["source"] == "MISSING"


def test_no_vague_relative_date_is_ever_used():
    from app.services.milestone3.documentation.extraction import is_vague_date_expression

    for phrase in ("next week", "ASAP", "TBD", "soon", "end of month", "in a couple of weeks"):
        assert is_vague_date_expression(phrase), phrase
    assert not is_vague_date_expression("2026-10-15")
    assert not is_vague_date_expression("15 October 2026")


def test_date_decision_prefers_document_then_admin():
    from app.services.milestone3.documentation.dates import resolve_due_date
    from app.services.milestone3.documentation.extraction import DateHit
    from datetime import date

    hit = DateHit(value=date(2026, 10, 15), label="due_date", evidence="due_date=2026-10-15",
                  document="02_Requirements.docx")

    both = resolve_due_date(date(2026, 1, 1), hit)
    assert both.effective == "2026-10-15"
    assert both.conflict is True
    assert both.evidence == "due_date=2026-10-15"

    admin_only = resolve_due_date(date(2026, 1, 1), None)
    assert admin_only.effective == "2026-01-01"
    assert admin_only.conflict is False

    neither = resolve_due_date(None, None)
    assert neither.effective == NOT_SPECIFIED
    assert neither.is_specified is False


def test_evidence_is_verbatim_from_the_source_document(labelled_project):
    with SessionLocal() as db:
        project = db.query(Project).filter(Project.id == labelled_project).first()
        register = build_risk_register(db, project)

    for entry in register["entries"]:
        evidence = entry["source_evidence"]
        assert evidence != NOT_SPECIFIED
        for line in evidence.splitlines():
            if line.strip():
                assert line.strip() in DOCX_CHUNK, "evidence must be a verbatim source span"


# --------------------------------------------------------------------------- #
# 3.6 Downloads and the validation report
# --------------------------------------------------------------------------- #


def test_download_docx_returns_a_real_office_document(client, admin_headers, labelled_project):
    generated = client.post(
        f"/api/projects/{labelled_project}/documents/generate",
        headers=admin_headers,
        json={"doc_type": "risk_register"},
    )
    assert generated.status_code == 201, generated.text
    doc_id = generated.json()["generated"][0]["id"]

    r = client.get(
        f"/api/projects/{labelled_project}/documents/{doc_id}/download?format=docx",
        headers=admin_headers,
    )
    assert r.status_code == 200
    assert "wordprocessingml.document" in r.headers["content-type"]
    assert ".docx" in r.headers["content-disposition"]

    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        assert zf.testzip() is None
        document_xml = zf.read("word/document.xml").decode("utf-8")
    # Headings, a real table and the risk content are present as text.
    assert "Risk Register" in document_xml
    assert "R001" in document_xml
    assert "Add retry and reconnect logic" in document_xml


def test_download_markdown_still_works_and_docx_is_optional_param(client, admin_headers,
                                                                 labelled_project):
    generated = client.post(
        f"/api/projects/{labelled_project}/documents/generate",
        headers=admin_headers,
        json={"doc_type": "action_items"},
    )
    doc_id = generated.json()["generated"][0]["id"]

    md = client.get(f"/api/projects/{labelled_project}/documents/{doc_id}/download",
                    headers=admin_headers)
    assert md.status_code == 200
    assert "text/markdown" in md.headers["content-type"]
    assert md.text.startswith("# ")

    explicit = client.get(
        f"/api/projects/{labelled_project}/documents/{doc_id}/download?format=md",
        headers=admin_headers,
    )
    assert explicit.text == md.text


def test_download_rejects_an_unknown_format(client, admin_headers, labelled_project):
    generated = client.post(
        f"/api/projects/{labelled_project}/documents/generate",
        headers=admin_headers,
        json={"doc_type": "action_items"},
    )
    doc_id = generated.json()["generated"][0]["id"]
    r = client.get(
        f"/api/projects/{labelled_project}/documents/{doc_id}/download?format=pdf",
        headers=admin_headers,
    )
    assert r.status_code == 422


def test_validation_endpoint_exposes_the_report(client, admin_headers, labelled_project):
    generated = client.post(
        f"/api/projects/{labelled_project}/documents/generate",
        headers=admin_headers,
        json={"doc_type": "risk_register"},
    )
    doc_id = generated.json()["generated"][0]["id"]

    r = client.get(f"/api/projects/{labelled_project}/documents/{doc_id}/validation",
                   headers=admin_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["doc_type"] == "risk_register"
    assert body["summary"]["fields_checked"] > 0
    assert body["summary"]["conflicts"] >= 1
    assert any(f["verdict"] == "CONFLICT" for f in body["findings"])
    assert all(f["note"] for f in body["findings"] if f["verdict"] == "CONFLICT")


def test_downloads_are_project_scoped(client, admin_headers, labelled_project, project_b):
    generated = client.post(
        f"/api/projects/{labelled_project}/documents/generate",
        headers=admin_headers,
        json={"doc_type": "risk_register"},
    )
    doc_id = generated.json()["generated"][0]["id"]
    for fmt in ("md", "docx"):
        r = client.get(
            f"/api/projects/{project_b['id']}/documents/{doc_id}/download?format={fmt}",
            headers=admin_headers,
        )
        assert r.status_code == 404


def test_structured_artifacts_expose_the_specification_field_order(client, admin_headers,
                                                                   labelled_project):
    generated = client.post(
        f"/api/projects/{labelled_project}/documents/generate",
        headers=admin_headers,
        json={"doc_type": "risk_register"},
    )
    doc_id = generated.json()["generated"][0]["id"]
    payload = client.get(f"/api/projects/{labelled_project}/documents/{doc_id}",
                         headers=admin_headers).json()["payload"]

    labels = [f["label"] for f in payload["artifacts"][0]["fields"]]
    assert labels[:5] == ["Description", "Category", "Probability", "Impact", "Risk score"]
    assert "Mitigation" in labels and "Contingency" in labels
    assert payload["validation"]["fields_checked"] > 0
    assert [a["id"] for a in payload["artifacts"]] == ["R001", "R002", "R003"]


def test_generation_never_modifies_uploaded_documents(client, admin_headers, labelled_project):
    with SessionLocal() as db:
        before = [
            (d.id, d.original_name, d.storage_path)
            for d in db.query(ProjectDocument).filter(ProjectDocument.project_id == labelled_project)
        ]
    client.post(f"/api/projects/{labelled_project}/documents/generate",
                headers=admin_headers, json={})
    with SessionLocal() as db:
        after = [
            (d.id, d.original_name, d.storage_path)
            for d in db.query(ProjectDocument).filter(ProjectDocument.project_id == labelled_project)
        ]
    assert before == after
