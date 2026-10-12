"""End-to-end pipeline and hallucination regression tests (Section 19, Step 7).

Everything here drives the *whole* product path with no live provider:

    upload -> parse -> chunk -> embed -> retrieve -> AI extraction
           -> evidence validation -> database persistence -> generated reports

``tests/fake_ai.py`` replaces only the two outbound seams (``ProviderManager.generate``
and the embedding provider), so the real orchestrator, validator, risk register
builder, action item builder, due-date resolver and document generator all run.
No production code is changed and ``backend/data/app.db`` is never touched.

The guarantees locked in here, one per Step 7 requirement:

1. The complete pipeline runs from one upload (req 1).
2. Source fidelity: every known risk / blocker / action / owner / date / priority
   / status lands in the correct field, exactly as the source states (req 2).
3. Hallucination prevention: a record with evidence absent from the retrieved
   text, or citing a document that was never uploaded, is dropped — not stored
   with an invented owner, deadline, priority, status or source (req 3).
4. Missing values stay unspecified and conflicting values are reported, never
   silently reconciled (req 4).
5. Evidence stays attached to the correct project document and an unsupported
   question gets the honest insufficient-evidence answer (req 5).
6. Repeated processing adds no duplicates and leaves manual records intact
   (req 6).
7. Every generated document contains only validated information and downloads as
   both Markdown and DOCX (req 7).
8. The intended heading is the uppercase product title, locked for all three
   document types (req 8).
"""

from __future__ import annotations

import io
import json
import zipfile

import pytest

from app.database import SessionLocal
from app.models import Blocker, Project, ProjectDocument, ProjectInsight, Risk, Task
from app.models_m3 import GeneratedDocument
from app.services.milestone3.assistant.service import INSUFFICIENT_EVIDENCE
from app.services.milestone3.common import NOT_SPECIFIED

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

EVIDENCE = "The MQTT gateway may be unreliable under load."

DOCX_TITLES = {
    "risk_register": "PROJECT RISK REGISTER",
    "user_stories": "PROJECT USER STORIES",
    "action_items": "PROJECT ACTION ITEMS",
}


# --------------------------------------------------------------------------- #
# offline AI — autouse so no test in this module can reach a real provider
# --------------------------------------------------------------------------- #


@pytest.fixture(autouse=True)
def offline_ai(monkeypatch):
    """Same deterministic seams as ``test_pipeline`` (kept local to the module)."""
    from app.services.ai.providers import ProviderManager

    import fake_ai

    def recording_generate(self, prompt, system=None, temperature=None, json_mode=False):
        return fake_ai.fake_generate(
            self, prompt, system=system, temperature=temperature, json_mode=json_mode
        )

    monkeypatch.setattr(ProviderManager, "generate", recording_generate)

    embedder = fake_ai.FakeEmbeddingProvider()
    monkeypatch.setattr("app.services.rag.get_embedding_provider", lambda: embedder)
    monkeypatch.setattr("app.services.documents.get_embedding_provider", lambda: embedder)
    monkeypatch.setattr("app.services.embeddings.get_embedding_provider", lambda: embedder)

    yield


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _new_project(client, headers, name, **body):
    """Bind ``client`` to a fresh anonymous workspace (creation via API is gone).

    Extra fields (e.g. ``expected_end_date``) are applied straight to the row so
    the due-date precedence cases can still set a project-record date.
    """
    from conftest import start_workspace

    pid = start_workspace(client)
    if body:
        from datetime import date as _date

        from app.database import SessionLocal

        with SessionLocal() as db:
            project = db.query(Project).filter(Project.id == pid).first()
            for key, value in body.items():
                if not hasattr(project, key):
                    continue
                if isinstance(value, str):
                    try:
                        value = _date.fromisoformat(value)
                    except ValueError:
                        pass
                setattr(project, key, value)
            db.commit()
    return {"id": pid, "name": name}


def _upload(client, headers, project_id, text=SPEC, filename="spec.txt"):
    r = client.post(
        f"/api/documents/upload?project_id={project_id}",
        headers=headers,
        files={"file": (filename, text.encode(), "text/plain")},
    )
    assert r.status_code in (200, 201), r.text
    return r.json()


def _status(client, headers, project_id):
    r = client.get(f"/api/projects/{project_id}/analysis/status", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def _run(client, headers, project_id):
    r = client.post(f"/api/projects/{project_id}/analysis/run", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def _db():
    return SessionLocal()


def _document_payload(client, headers, project_id, doc_type):
    listing = client.get(f"/api/projects/{project_id}/documents", headers=headers).json()
    doc = next(d for d in listing["documents"] if d["doc_type"] == doc_type)
    detail = client.get(
        f"/api/projects/{project_id}/documents/{doc['id']}", headers=headers
    ).json()
    return doc, detail


def _entry(entries, key, value):
    return next(e for e in entries if e[key] == value)


def _risk_count(project_id):
    with _db() as db:
        return db.query(Risk).filter(Risk.project_id == project_id).count()


# --------------------------------------------------------------------------- #
# 1 + 2 + 3: the full pipeline, source fidelity, no invention
# --------------------------------------------------------------------------- #


def test_end_to_end_pipeline_persists_every_value_exactly(client, admin_headers):
    project = _new_project(client, admin_headers, "E2E Fidelity Project")
    uploaded = _upload(client, admin_headers, project["id"])
    pid = project["id"]

    status = _status(client, admin_headers, pid)
    assert status["status"] in ("Completed", "CompletedWithWarnings")
    assert status["counts"]["documents_processed"] >= 1
    assert status["counts"]["generated_documents"] == 3

    with _db() as db:
        doc_id = (
            db.query(ProjectDocument)
            .filter(ProjectDocument.project_id == pid)
            .one()
            .id
        )
        risks = {r.title: r for r in db.query(Risk).filter(Risk.project_id == pid).all()}
        blockers = {b.title: b for b in db.query(Blocker).filter(Blocker.project_id == pid).all()}
        tasks = {t.title: t for t in db.query(Task).filter(Task.project_id == pid).all()}

    # -- risks --------------------------------------------------------------- #
    # The agents normalise levels to upper case in the database; the reports
    # title-case them back for display.
    gw = risks["MQTT gateway reliability"]
    assert (gw.severity, gw.probability, gw.impact) == ("HIGH", "MEDIUM", "HIGH")
    assert gw.evidence == EVIDENCE
    assert gw.status == "Open"
    assert gw.source_type == "ai_detected"
    assert gw.source_document_id == doc_id
    assert "Sensor battery life" in risks

    # -- blocker: no owner is stated, so none is invented -------------------- #
    blk = blockers["Cloud budget approval outstanding"]
    assert blk.severity == "HIGH"
    assert blk.owner == ""  # missing stays missing, never "Unassigned"
    assert blk.evidence == "Cloud budget approval is outstanding."
    assert blk.source_document_id == doc_id
    assert blk.status == "Open"

    # -- tasks: deadline/priority preserved, unmatched owner never invented --- #
    finalise = tasks["Finalise the sensor datasheet"]
    assert finalise.priority == "HIGH"
    assert finalise.due_date is not None and finalise.due_date.date().isoformat() == "2026-10-15"
    assert finalise.assigned_to is None  # "Priya" is not a project member
    assert finalise.description == "Assigned to: Priya"
    assert finalise.ai_generated is True

    publish = tasks["Publish the API contract"]
    assert publish.priority == "MEDIUM"
    assert publish.due_date is None  # no deadline stated -> none stored

    # -- scope insight ------------------------------------------------------- #
    with _db() as db:
        goal = (
            db.query(ProjectInsight)
            .filter(
                ProjectInsight.project_id == pid,
                ProjectInsight.agent == "scope",
                ProjectInsight.category == "project_goal",
            )
            .one()
        )
    assert "Smart Campus IoT platform" in goal.summary


def test_generated_reports_place_values_in_the_correct_fields(client, admin_headers):
    project = _new_project(client, admin_headers, "E2E Field Placement")
    _upload(client, admin_headers, project["id"])
    pid = project["id"]

    _doc, register = _document_payload(client, admin_headers, pid, "risk_register")
    entry = _entry(register["payload"]["entries"], "title", "MQTT gateway reliability")
    assert entry["probability"] == "Medium"
    assert entry["impact"] == "High"
    assert entry["severity"] == "High"
    assert entry["status"] == "Open"
    assert entry["owner"] == NOT_SPECIFIED  # risks carry no owner; none invented
    assert EVIDENCE in entry["source_evidence"]  # verbatim span of the source document
    assert entry["source_document"] != NOT_SPECIFIED
    assert entry["risk_score"] != NOT_SPECIFIED
    assert 1 <= entry["risk_score_value"] <= 25

    _doc, actions = _document_payload(client, admin_headers, pid, "action_items")
    action = _entry(actions["payload"]["entries"], "action", "Finalise the sensor datasheet")
    assert action["priority"] == "High"
    assert action["due_date"] == "2026-10-15"
    assert action["source_document"] != NOT_SPECIFIED


def test_hallucinated_records_are_rejected_end_to_end(client, admin_headers, monkeypatch):
    """A fabricated risk and a risk citing a never-uploaded file must both vanish."""
    import fake_ai
    from app.services.ai.providers import ProviderManager

    def generate_with_hallucinations(self, prompt, system=None, temperature=None, json_mode=False):
        if prompt.startswith("Identify project risks from the context."):
            payload = {
                "risks": list(fake_ai._RISK_RESULT["risks"])
                + [
                    {
                        "title": "Fabricated data centre dragon",
                        "description": "A dragon guards the servers.",
                        "severity": "Critical",
                        "probability": "High",
                        "impact": "High",
                        "evidence": "A dragon guards the servers and eats network cables.",
                        "source_document": "spec.txt",
                        "source_page": None,
                        "source_section": None,
                        "recommended_action": "Hire a knight.",
                    },
                    {
                        "title": "Phantom document risk",
                        "description": "Described only in a file that was never uploaded.",
                        "severity": "High",
                        "probability": "High",
                        "impact": "High",
                        "evidence": EVIDENCE,  # real quote, but...
                        "source_document": "ghost.txt",  # ...unresolvable citation
                        "source_page": None,
                        "source_section": None,
                        "recommended_action": "Ignore.",
                    },
                ]
            }
            return json.dumps(payload), fake_ai.FAKE_PROVIDER, fake_ai.FAKE_MODEL
        return fake_ai.fake_generate(
            self, prompt, system=system, temperature=temperature, json_mode=json_mode
        )

    monkeypatch.setattr(ProviderManager, "generate", generate_with_hallucinations)

    project = _new_project(client, admin_headers, "E2E Hallucination Project")
    _upload(client, admin_headers, project["id"])
    pid = project["id"]

    with _db() as db:
        titles = [r.title for r in db.query(Risk).filter(Risk.project_id == pid).all()]

    assert "MQTT gateway reliability" in titles  # grounded records survive
    assert not any("dragon" in t.lower() for t in titles)
    assert not any("phantom" in t.lower() for t in titles)

    # Nothing citing the phantom file leaked into the generated register either.
    _doc, register = _document_payload(client, admin_headers, pid, "risk_register")
    register_titles = [e["title"].lower() for e in register["payload"]["entries"]]
    assert not any("phantom" in t for t in register_titles)
    assert all("ghost.txt" not in json.dumps(e) for e in register["payload"]["entries"])


# --------------------------------------------------------------------------- #
# 4: missing stays missing, conflicts are reported not reconciled
# --------------------------------------------------------------------------- #


def test_missing_values_stay_unspecified_in_api_and_database(client, admin_headers):
    project = _new_project(client, admin_headers, "E2E Missing Values")
    _upload(client, admin_headers, project["id"])
    pid = project["id"]

    blockers = client.get(f"/api/blockers?project_id={pid}", headers=admin_headers).json()
    assert blockers and all(b["owner"] == "" for b in blockers)

    tasks = client.get(f"/api/tasks?project_id={pid}", headers=admin_headers).json()
    undated = next(t for t in tasks if t["title"] == "Publish the API contract")
    assert undated["due_date"] is None
    assert undated["assigned_to"] is None
    assert undated["priority"] == "MEDIUM"  # stated by the extraction, kept upper case

    _doc, actions = _document_payload(client, admin_headers, pid, "action_items")
    entry = _entry(actions["payload"]["entries"], "action", "Publish the API contract")
    assert entry["due_date"] == NOT_SPECIFIED
    assert entry["due_date_assigned"] is False


def test_conflicting_due_date_is_reported_not_reconciled(client, admin_headers):
    project = _new_project(
        client, admin_headers, "E2E Due Date Conflict", expected_end_date="2026-01-15"
    )
    _upload(client, admin_headers, project["id"])
    pid = project["id"]

    due = _status(client, admin_headers, pid)["due_date"]
    assert due["conflict"] is True
    assert due["effective_date"] == "2026-12-31"  # document wins, admin kept for audit
    assert due["admin_date"] == "2026-01-15"

    _doc, register = _document_payload(client, admin_headers, pid, "risk_register")
    payload_due = register["payload"]["project_due_date"]
    assert payload_due["conflict"] is True
    assert payload_due["effective_date"] == "2026-12-31"


# --------------------------------------------------------------------------- #
# 5: evidence from the correct document; unsupported question refused
# --------------------------------------------------------------------------- #


def test_evidence_is_grounded_in_the_projects_own_document(client, admin_headers):
    project = _new_project(client, admin_headers, "E2E Evidence Grounding")
    _upload(client, admin_headers, project["id"])
    pid = project["id"]

    risks = client.get(f"/api/risks?project_id={pid}", headers=admin_headers).json()
    assert risks
    for risk in risks:
        assert risk["source_document"] == "spec.txt"
        assert risk["source_document_id"] is not None
        assert risk["evidence"] and risk["evidence"] in SPEC


def test_unsupported_question_returns_insufficient_evidence(client, admin_headers, monkeypatch):
    project = _new_project(client, admin_headers, "E2E Honest Assistant")
    _upload(client, admin_headers, project["id"])
    pid = project["id"]

    # No chunk is retrieved and the question matches no structured table, so the
    # only honest answer is the specification's insufficient-evidence sentence.
    monkeypatch.setattr(
        "app.services.milestone3.assistant.service.retrieve_chunks",
        lambda *a, **k: [],
    )
    r = client.post(
        f"/api/projects/{pid}/assistant/ask",
        headers=admin_headers,
        json={"question": "What colour is the logo?"},
    )
    assert r.status_code in (200, 201), r.text
    body = r.json()
    assert body["answer"] == INSUFFICIENT_EVIDENCE
    assert body["grounded"] is False
    assert body["insufficient_evidence"] is True


# --------------------------------------------------------------------------- #
# 6: repeated processing — no duplicates, manual records preserved
# --------------------------------------------------------------------------- #


def test_repeated_processing_preserves_manual_records_without_duplicates(client, admin_headers):
    project = _new_project(client, admin_headers, "E2E Repeat Project")
    pid = project["id"]

    manual_risk = client.post(
        f"/api/risks?project_id={pid}",
        headers=admin_headers,
        json={"title": "Human-entered risk", "severity": "Low", "probability": "Low", "impact": "Low"},
    ).json()
    manual_task = client.post(
        f"/api/tasks?project_id={pid}",
        headers=admin_headers,
        json={"title": "Human-entered task", "priority": "High", "status": "In Progress"},
    ).json()

    _upload(client, admin_headers, pid)

    def snapshot():
        with _db() as db:
            return {
                "risks": db.query(Risk).filter(Risk.project_id == pid).count(),
                "tasks": db.query(Task).filter(Task.project_id == pid).count(),
                "blockers": db.query(Blocker).filter(Blocker.project_id == pid).count(),
                "generated": db.query(GeneratedDocument)
                .filter(GeneratedDocument.project_id == pid)
                .count(),
            }

    before = snapshot()
    _run(client, admin_headers, pid)
    _run(client, admin_headers, pid)
    after = snapshot()

    assert after == before
    assert after["generated"] == 3

    with _db() as db:
        assert db.query(Risk).filter(Risk.id == manual_risk["id"]).first() is not None
        assert db.query(Task).filter(Task.id == manual_task["id"]).first() is not None


# --------------------------------------------------------------------------- #
# 7 + 8: generated documents download and carry the intended uppercase heading
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("doc_type", ["risk_register", "user_stories", "action_items"])
def test_generated_documents_download_and_lock_the_intended_heading(
    client, admin_headers, doc_type
):
    project = _new_project(client, admin_headers, f"E2E Download {doc_type}")
    _upload(client, admin_headers, project["id"])
    pid = project["id"]

    doc, detail = _document_payload(client, admin_headers, pid, doc_type)
    title = DOCX_TITLES[doc_type]

    # Markdown: first line is the uppercase product heading (req 8).
    assert detail["content"].splitlines()[0] == f"# {title}"

    md = client.get(
        f"/api/projects/{pid}/documents/{doc['id']}/download", headers=admin_headers
    )
    assert md.status_code == 200
    assert md.text.splitlines()[0] == f"# {title}"

    docx = client.get(
        f"/api/projects/{pid}/documents/{doc['id']}/download?format=docx",
        headers=admin_headers,
    )
    assert docx.status_code == 200
    assert "wordprocessingml.document" in docx.headers["content-type"]
    with zipfile.ZipFile(io.BytesIO(docx.content)) as zf:
        assert zf.testzip() is None
        document_xml = zf.read("word/document.xml").decode("utf-8")
    assert title in document_xml
