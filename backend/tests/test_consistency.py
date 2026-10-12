"""Database, backend and frontend consistency (Section 19, Step 6).

The guarantees locked in here:

1. Field mapping across the source documentation -> AI extraction -> validation
   -> database -> API -> generated report path preserves every value the source
   and the extraction produced. Nothing is swapped, defaulted or invented on the
   way through.
2. Missing values stay missing (empty strings / NOT_SPECIFIED), independent of
   the NOT NULL default columns on the database.
3. Repeated analyses never duplicate risks, blockers, tasks, user story sources
   or health metrics rows.
4. Projects are isolated: reports, document indexes and API listings never leak
   records from another project, and cross-project source attribution is
   rejected.
5. Regenerating a generated document upserts a single row instead of adding
   duplicates.

These tests run against the same temporary database every other test uses, so
they never touch ``backend/data/app.db``.
"""

from types import SimpleNamespace

import pytest

from app.database import SessionLocal
from app.models import AiRun, Project, ProjectDocument, ProjectInsight, ProjectMember, Risk, Role, Task, User
from app.models_m3 import GeneratedDocument
from app.services.agents.orchestrator import (
    AGENT_ORDER,
    clear_agent_outputs,
    persist_actions,
    persist_health_metrics,
    persist_risks,
)
from app.services.milestone3.documentation.generator import generate_document
from app.services.milestone3.documentation.risk_register import build_risk_register

EVIDENCE = "The MQTT gateway may be unreliable under load."


@pytest.fixture()
def seeded(client):
    """The client's own workspace project, a processed document, and members.

    Binds the shared ``client`` to a fresh anonymous workspace so the API-
    driven tests in this module (risk/task CRUD) target the caller's own
    project, then seeds the supporting rows directly.
    """
    from conftest import start_workspace

    pid = start_workspace(client)
    with SessionLocal() as db:
        project = db.query(Project).filter(Project.id == pid).one()
        project.name = "Consistency Project"
        owner = _make_user(db, "Owner")
        ada = _make_user(db, "Ada")
        _make_member(db, project, ada)
        doc = _make_document(db, project)
        db.commit()
        doc_id = doc.id
        project_id = project.id
        owner_id = owner.id
        ada_id = ada.id
    return {
        "project_id": project_id,
        "document_id": doc_id,
        "owner_id": owner_id,
        "ada_id": ada_id,
    }


def _make_ai_run(db, project_id: int, agent: str) -> int:
    run = AiRun(project_id=project_id, agent=agent)
    db.add(run)
    db.commit()
    db.refresh(run)
    return run.id


def _make_user(db, name: str) -> User:
    email = f"{name.lower()}@example.com"
    existing = db.query(User).filter(User.email == email).first()
    if existing:
        return existing
    role = db.query(Role).order_by(Role.id).first()
    user = User(email=email, password_hash="x", name=name, role_id=role.id)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _make_project(db, name: str) -> Project:
    project = Project(name=name, description="", objective="", priority="Medium", status="Active")
    db.add(project)
    db.commit()
    db.refresh(project)
    return project


def _make_member(db, project: Project, user: User) -> ProjectMember:
    member = ProjectMember(project_id=project.id, user_id=user.id)
    db.add(member)
    db.commit()
    db.refresh(member)
    return member


def _make_document(db, project: Project, name: str = "spec.txt") -> ProjectDocument:
    owner = db.query(User).order_by(User.id).first()
    doc = ProjectDocument(
        project_id=project.id,
        file_name=name,
        original_name=name,
        file_type="txt",
        mime_type="text/plain",
        file_size=10,
        storage_path=f"/tmp/{project.id}/{name}",
        uploaded_by=owner.id,
        status="Processed",
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)
    return doc


def _chunks(document_id: int, name: str = "spec.txt"):
    return [{"document_id": document_id, "original_name": name, "text": EVIDENCE}]


def _risk_item(title, severity="High", probability="Medium", impact="Medium"):
    return SimpleNamespace(
        title=title,
        description=f"Context for {title}.",
        severity=severity,
        probability=probability,
        impact=impact,
        evidence=EVIDENCE,
        source_document="spec.txt",
        recommended_action="Review before release",
    )


def _action_item(action, assigned="", deadline="2026-12-01", priority="Medium"):
    return SimpleNamespace(
        action=action,
        assigned_person=assigned,
        deadline=deadline,
        priority=priority,
        evidence=EVIDENCE,
        source_document="spec.txt",
        source_page=None,
        source_section=None,
    )


def _risk_count(db, project_id: int, source_type: str) -> int:
    return (
        db.query(Risk)
        .filter(Risk.project_id == project_id, Risk.source_type == source_type)
        .count()
    )


def _ai_task_count(db, project_id: int) -> int:
    return (
        db.query(Task)
        .filter(Task.project_id == project_id, Task.ai_generated == True)  # noqa: E712
        .count()
    )


def _health_count(db, project_id: int) -> int:
    return (
        db.query(ProjectInsight)
        .filter(ProjectInsight.project_id == project_id, ProjectInsight.agent == "health")
        .count()
    )


# --------------------------------------------------------------------------- #
# repeated runs never duplicate records (requirement 5)
# --------------------------------------------------------------------------- #


def test_analysis_run_twice_does_not_duplicate_records(seeded):
    project_id = seeded["project_id"]
    with SessionLocal() as db:
        chunks = _chunks(seeded["document_id"])
        risk_run = _make_ai_run(db, project_id, "risk")
        action_run = _make_ai_run(db, project_id, "action")
        for _run in range(2):
            clear_agent_outputs(db, project_id, AGENT_ORDER)
            persist_risks(
                db, project_id, risk_run,
                SimpleNamespace(risks=[_risk_item("Gateway connectivity may degrade")]),
                chunks,
            )
            persist_actions(
                db, project_id, action_run,
                SimpleNamespace(
                    action_items=[
                        _action_item("Upgrade gateway firmware", assigned="Ada", priority="High")
                    ]
                ),
                chunks, created_by=None,
            )
            persist_health_metrics(db, project_id)
            db.commit()

        assert _risk_count(db, project_id, "ai_detected") == 1
        assert _ai_task_count(db, project_id) == 1
        assert _health_count(db, project_id) == 1
        health_rows = (
            db.query(ProjectInsight)
            .filter(ProjectInsight.project_id == project_id, ProjectInsight.agent == "health")
            .all()
        )
        assert len(health_rows) == 1


def test_health_metrics_row_is_replaced_not_accumulated(seeded):
    project_id = seeded["project_id"]
    with SessionLocal() as db:
        for _ in range(3):
            persist_health_metrics(db, project_id)
            db.commit()
        assert _health_count(db, project_id) == 1


def test_manual_records_survive_rerun_clearing(seeded):
    project_id = seeded["project_id"]
    owner_id = seeded["owner_id"]
    with SessionLocal() as db:
        manual_risk = Risk(
            project_id=project_id, title="Human-entered risk", severity="Low",
            probability="Low", impact="Low", status="Open", source_type="manual",
            created_by=owner_id,
        )
        manual_task = Task(
            project_id=project_id, title="Human-entered task", status="In Progress",
            priority="High", source_type="manual", ai_generated=False, created_by=owner_id,
        )
        db.add_all([manual_risk, manual_task])
        db.commit()

        clear_agent_outputs(db, project_id, AGENT_ORDER)

        assert _risk_count(db, project_id, "manual") == 1
        assert db.query(Task).filter(
            Task.project_id == project_id, Task.ai_generated == False  # noqa: E712
        ).count() == 1
        assert db.query(Task).filter(Task.project_id == project_id).count() == 1


# --------------------------------------------------------------------------- #
# field mapping and missing values (requirements 2, 3 and 4)
# --------------------------------------------------------------------------- #


def test_ai_risk_fields_stored_verbatim(seeded):
    project_id = seeded["project_id"]
    with SessionLocal() as db:
        chunks = _chunks(seeded["document_id"])
        run_id = _make_ai_run(db, project_id, "risk")
        persist_risks(
            db, project_id, run_id,
            SimpleNamespace(
                risks=[
                    _risk_item(
                        "Gateway latency causes missed readings",
                        severity="Critical", probability="High", impact="High",
                    )
                ]
            ),
            chunks,
        )
        db.commit()
        risk = db.query(Risk).filter(Risk.project_id == project_id).one()
        assert risk.severity == "Critical"
        assert risk.probability == "High"
        assert risk.impact == "High"
        assert risk.status == "Open"  # workflow state, not a source value
        assert risk.source_type == "ai_detected"
        assert risk.source_document_id == seeded["document_id"]
        assert risk.evidence == EVIDENCE


def test_ai_task_missing_priority_stays_empty(seeded):
    project_id = seeded["project_id"]
    with SessionLocal() as db:
        chunks = _chunks(seeded["document_id"])
        run_id = _make_ai_run(db, project_id, "action")
        persist_actions(
            db, project_id, run_id,
            SimpleNamespace(
                action_items=[
                    _action_item("Confirm cloud budget approval", assigned="Ada", priority="")
                ]
            ),
            chunks, created_by=None,
        )
        db.commit()
        task = db.query(Task).filter(Task.project_id == project_id).one()
        assert task.priority == ""
        assert task.assigned_to == seeded["ada_id"]
        assert "Assigned to:" not in task.description


def test_ai_task_unmatched_assignee_preserved_in_description(seeded):
    project_id = seeded["project_id"]
    with SessionLocal() as db:
        chunks = _chunks(seeded["document_id"])
        run_id = _make_ai_run(db, project_id, "action")
        persist_actions(
            db, project_id, run_id,
            SimpleNamespace(
                action_items=[
                    _action_item("File the access request", assigned="Unknown Vendor", priority="Low")
                ]
            ),
            chunks, created_by=None,
        )
        db.commit()
        task = db.query(Task).filter(Task.project_id == project_id).one()
        assert task.priority == "Low"
        assert task.assigned_to is None
        assert task.description == "Assigned to: Unknown Vendor"


def test_manual_risk_values_roundtrip_through_api(seeded, client):
    project_id = seeded["project_id"]
    payload = {
        "title": "Cloud budget approval outstanding",
        "description": "Approval has not been signed.",
        "severity": "Critical",
        "probability": "High",
        "impact": "High",
        "status": "Open",
        "recommended_action": "Escalate to sponsor",
    }
    res = client.post(f"/api/risks?project_id={project_id}", json=payload)
    assert res.status_code == 201, res.text
    created = res.json()
    for field, value in payload.items():
        assert created[field] == value, f"{field}: {created[field]!r} != {value!r}"

    listing = client.get(f"/api/risks?project_id={project_id}").json()
    assert listing and listing[0]["title"] == payload["title"]
    for field, value in payload.items():
        assert listing[0][field] == value


def test_task_update_can_clear_priority_and_status(seeded, client):
    project_id = seeded["project_id"]
    created = client.post(
        f"/api/tasks?project_id={project_id}",
        json={"title": "Temporary follow-up", "priority": "High", "status": "In Progress"},
    ).json()
    assert created["priority"] == "High"
    assert created["status"] == "In Progress"

    updated = client.put(
        f"/api/tasks/{created['id']}",
        json={"priority": "", "status": ""},
    ).json()
    assert updated["priority"] == ""
    assert updated["status"] == ""
    with SessionLocal() as db:
        row = db.query(Task).filter(Task.id == created["id"]).one()
        assert row.priority == ""
        assert row.status == ""


# --------------------------------------------------------------------------- #
# project isolation (requirement 6)
# --------------------------------------------------------------------------- #


def _seeded_project(db, name: str, title: str, severity="High") -> tuple[int, int]:
    project = _make_project(db, name)
    doc = _make_document(db, project, name=f"{name.lower()}.txt")
    risk = Risk(
        project_id=project.id, title=title, description=title,
        severity=severity, probability="High", impact="High",
        status="Open", source_type="manual",
    )
    db.add(risk)
    db.commit()
    return project.id, doc.id


def test_risk_register_never_leaks_across_projects():
    with SessionLocal() as db:
        project_a_id, _doc_a = _seeded_project(db, "Isolation A", "Gateway connectivity may degrade")
        _project_b_id, _doc_b = _seeded_project(db, "Isolation B", "Weather station sensors need calibration")

        project_a = db.query(Project).filter(Project.id == project_a_id).one()
        register = build_risk_register(db, project_a)
        titles = [e["title"] for e in register["entries"]]
        assert register["total"] == 1
        assert "Gateway connectivity may degrade" in titles
        assert "Weather station sensors need calibration" not in titles
        assert all(e["risk_id"].startswith("RISK-") for e in register["entries"])


def test_list_endpoints_scoped_by_project(seeded, client, fresh_client):
    project_id = seeded["project_id"]
    other_id = fresh_client.get("/api/session/current").json()["project_id"]
    mine = client.post(
        f"/api/risks?project_id={project_id}",
        json={"title": "My own risk", "severity": "Medium", "probability": "Medium", "impact": "Medium"},
    )
    assert mine.status_code == 201, mine.text
    other = fresh_client.post(
        f"/api/risks?project_id={other_id}",
        json={"title": "Other risk", "severity": "Medium", "probability": "Medium", "impact": "Medium"},
    )
    assert other.status_code == 201, other.text
    a_only = client.get(f"/api/risks?project_id={project_id}").json()
    b_only = fresh_client.get(f"/api/risks?project_id={other_id}").json()
    assert a_only and all(r["project_id"] == project_id for r in a_only)
    assert b_only and all(r["project_id"] == other_id for r in b_only)
    assert all("Other risk" != r["title"] for r in a_only)
    # A workspace cannot read another workspace's risks at all.
    assert client.get(f"/api/risks?project_id={other_id}").status_code == 404


def test_source_document_must_belong_to_project(seeded, client):
    project_id = seeded["project_id"]
    with SessionLocal() as db:
        other_id, other_doc_id = _seeded_project(db, "Isolation D", "Unrelated risk")
    task = client.post(
        f"/api/tasks?project_id={project_id}",
        json={"title": "Cross-project attribution check"},
    ).json()
    bad = client.put(
        f"/api/tasks/{task['id']}",
        json={"source_document_id": other_doc_id},
    )
    assert bad.status_code == 400
    good = client.put(
        f"/api/tasks/{task['id']}",
        json={"source_document_id": None},
    )
    assert good.status_code == 200
    assert good.json()["source_document_id"] is None


# --------------------------------------------------------------------------- #
# generated docs upsert on regeneration (requirement 5)
# --------------------------------------------------------------------------- #


def test_regenerated_document_upserts_single_row(seeded):
    project_id = seeded["project_id"]
    with SessionLocal() as db:
        project = db.query(Project).filter(Project.id == project_id).one()
        first = generate_document(db, project, "risk_register")
        second = generate_document(db, project, "risk_register")
        rows = (
            db.query(GeneratedDocument)
            .filter(GeneratedDocument.project_id == project_id)
            .all()
        )
        assert len(rows) == 1
        assert rows[0].id == first.id == second.id
        assert rows[0].generation_count == 2