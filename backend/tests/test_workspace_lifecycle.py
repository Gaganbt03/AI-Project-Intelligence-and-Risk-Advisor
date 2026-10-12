"""Lifecycle tests for the anonymous temporary workspace (Step 5).

Covers the isolation boundary that matters for cleanup:

* two browsers get independent workspaces and cannot see each other's project;
* `POST /api/session/finish` permanently deletes every row, file and vector;
* `POST /api/session/close` only *arms* a grace purge, and any request inside
  that window revives the session (quick refresh must not lose work);
* the sweeper reclaims closing sessions past grace and active sessions past TTL.

A raw ``TestClient`` is used per test so each one gets its own cookie jar (a
separate "browser"); the app lifespan is not entered, so the background sweeper
does not race the assertions.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.config import get_settings
from app.database import SessionLocal
from app.main import app
from app.models import AuditLog, DocumentChunk, Project, ProjectDocument, Risk, User
from app.models_sessions import WorkspaceSession
from app.services import sessions as session_service
from app.services.documents import process_and_analyze
from app.services.vector_store import get_vector_store


def _browser() -> TestClient:
    return TestClient(app)


def _start(client: TestClient) -> dict:
    res = client.post("/api/session/start")
    assert res.status_code == 200, res.text
    return res.json()


def test_two_browsers_get_isolated_workspaces():
    a, b = _browser(), _browser()
    sa, sb = _start(a), _start(b)
    assert sa["project_id"] != sb["project_id"]

    projects_a = a.get("/api/projects").json()
    projects_b = b.get("/api/projects").json()
    assert [p["id"] for p in projects_a] == [sa["project_id"]]
    assert [p["id"] for p in projects_b] == [sb["project_id"]]

    assert a.get("/api/documents").json() == []
    assert b.get("/api/documents").json() == []

    # Cross-session access is reported as 404, never leaking existence.
    assert a.get(f"/api/projects/{sb['project_id']}").status_code == 404
    assert b.get(f"/api/projects/{sa['project_id']}").status_code == 404

    # No cookie at all is unauthenticated.
    anon = _browser()
    assert anon.get("/api/projects").status_code == 401
    assert anon.get("/api/documents").status_code == 401


def _seed_workspace_data(project_id: int, session_id: int) -> None:
    """Insert one row per notable table plus a file and a vector."""
    settings = get_settings()
    with SessionLocal() as db:
        user_id = db.query(User).order_by(User.id.asc()).first().id
        doc = ProjectDocument(
            project_id=project_id,
            file_name="seed.pdf",
            original_name="seed.pdf",
            file_type="pdf",
            file_size=3,
            storage_path=str(Path(settings.UPLOAD_DIR) / str(project_id) / "seed.pdf"),
            uploaded_by=user_id,
            status="Processed",
        )
        db.add(doc)
        db.commit()
        db.refresh(doc)
        doc_id = doc.id
        db.add(
            DocumentChunk(
                document_id=doc.id, project_id=project_id, chunk_index=0, text="hello world"
            )
        )
        db.add(
            Risk(
                project_id=project_id,
                title="Seed risk",
                source_document_id=doc.id,
                created_by=user_id,
            )
        )
        db.add(
            AuditLog(
                user_id=user_id,
                user_email="",
                action="seed",
                resource_type="project",
                resource_id=str(project_id),
                project_id=project_id,
            )
        )
        db.commit()

    upload_dir = Path(settings.UPLOAD_DIR) / str(project_id)
    upload_dir.mkdir(parents=True, exist_ok=True)
    (upload_dir / "seed.pdf").write_bytes(b"%PDF")

    vs = get_vector_store()
    chunk = SimpleNamespace(
        chunk_index=0, page_number=1, section="Intro", row_number=None, text="hello world"
    )
    vs.add_document_chunks(project_id, doc_id, "seed.pdf", [chunk], [[0.1, 0.2, 0.3]])
    assert vs.count(project_id) == 1


def _assert_workspace_gone(project_id: int, session_id: int) -> None:
    settings = get_settings()
    with SessionLocal() as db:
        assert db.query(Project).filter(Project.id == project_id).first() is None
        assert (
            db.query(WorkspaceSession).filter(WorkspaceSession.id == session_id).first() is None
        )
        assert db.query(ProjectDocument).filter(ProjectDocument.project_id == project_id).count() == 0
        assert db.query(DocumentChunk).filter(DocumentChunk.project_id == project_id).count() == 0
        assert db.query(Risk).filter(Risk.project_id == project_id).count() == 0
        assert db.query(AuditLog).filter(AuditLog.project_id == project_id).count() == 0

    assert not (Path(settings.UPLOAD_DIR) / str(project_id)).exists()
    assert get_vector_store().count(project_id) == 0


def test_finish_purges_all_workspace_data():
    a = _browser()
    session = _start(a)
    _seed_workspace_data(session["project_id"], session["session_id"])

    res = a.post("/api/session/finish")
    assert res.status_code == 200, res.text
    assert res.json()["ok"] is True
    assert res.json()["purged"]["projects"] == 1

    _assert_workspace_gone(session["project_id"], session["session_id"])

    # The cookie is cleared, so the browser is anonymous again.
    assert a.get("/api/session/current").status_code == 401
    assert a.get("/api/projects").status_code == 401


def test_close_then_request_within_grace_revives():
    a = _browser()
    session = _start(a)
    sid = session["session_id"]

    assert a.post("/api/session/close").json()["ok"] is True
    with SessionLocal() as db:
        assert db.get(WorkspaceSession, sid).status == session_service.CLOSING

    # Any real request cancels the pending purge.
    assert a.get("/api/session/current").status_code == 200
    with SessionLocal() as db:
        assert db.get(WorkspaceSession, sid).status == session_service.ACTIVE


def test_sweeper_purges_closing_session_past_grace():
    a = _browser()
    session = _start(a)
    a.post("/api/session/close")

    with SessionLocal() as db:
        purged = session_service.sweep_workspaces(db, grace_seconds=-1)
    assert purged["closed"] == 1
    assert session["project_id"] in purged["projects"]
    _assert_workspace_gone(session["project_id"], session["session_id"])


def test_sweeper_purges_idle_expired_session():
    a = _browser()
    session = _start(a)

    with SessionLocal() as db:
        row = db.get(WorkspaceSession, session["session_id"])
        row.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        db.commit()

    with SessionLocal() as db:
        purged = session_service.sweep_workspaces(db)
    assert purged["expired"] == 1
    assert session["project_id"] in purged["projects"]
    _assert_workspace_gone(session["project_id"], session["session_id"])


def test_finish_leaves_another_workspace_untouched():
    """Explicit cleanup of one workspace must never touch a concurrent one."""
    a, b = _browser(), _browser()
    sa, sb = _start(a), _start(b)
    _seed_workspace_data(sa["project_id"], sa["session_id"])
    _seed_workspace_data(sb["project_id"], sb["session_id"])

    assert a.post("/api/session/finish").json()["ok"] is True
    _assert_workspace_gone(sa["project_id"], sa["session_id"])

    # B is completely unaffected: session, project, rows, file and vectors survive.
    with SessionLocal() as db:
        assert db.query(Project).filter(Project.id == sb["project_id"]).first() is not None
        assert (
            db.query(WorkspaceSession).filter(WorkspaceSession.id == sb["session_id"]).first()
            is not None
        )
        assert db.query(ProjectDocument).filter(
            ProjectDocument.project_id == sb["project_id"]
        ).count() == 1
        assert db.query(Risk).filter(Risk.project_id == sb["project_id"]).count() == 1
    assert (Path(get_settings().UPLOAD_DIR) / str(sb["project_id"])).exists()
    assert get_vector_store().count(sb["project_id"]) == 1
    assert b.get("/api/session/current").status_code == 200
    assert [p["id"] for p in b.get("/api/projects").json()] == [sb["project_id"]]


def test_pipeline_guard_skips_vanished_document():
    """A background pipeline whose workspace was purged must not raise or recreate."""
    with SessionLocal() as db:
        assert process_and_analyze(db, 9_999_999) is None
