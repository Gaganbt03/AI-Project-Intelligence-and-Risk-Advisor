import os
import tempfile
from pathlib import Path

import pytest

# ---- Isolate all side effects BEFORE importing any app module ----
_tmp = Path(tempfile.mkdtemp(prefix="apir_tests_"))
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp / 'test.db'}"
os.environ["UPLOAD_DIR"] = str(_tmp / "uploads")
os.environ["VECTOR_DB_PATH"] = str(_tmp / "vector_db")
os.environ["SECRET_KEY"] = "test-secret-key-not-for-production"
os.environ["AI_TEMPERATURE"] = "0.0"
# The offline fake embedder produces arbitrary cosine distances, so the semantic
# relevance floor would starve the pipeline of evidence. Disable filtering here;
# the filter itself is covered directly in tests/test_rag_relevance.py.
os.environ["RAG_MIN_SIMILARITY"] = "-1.0"

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.database import SessionLocal, init_db  # noqa: E402
from app.models import User  # noqa: E402
from app.services.bootstrap import ensure_roles  # noqa: E402

init_db()
with SessionLocal() as _db:
    ensure_roles(_db)


# --------------------------------------------------------------------------- #
# Session-based fixtures.
#
# The product is login-free: a browser is bound to exactly one private,
# ephemeral workspace through an HttpOnly cookie. Tests therefore act through a
# `client` whose cookie jar is bound to a single workspace ("project_a"), while
# `project_b` is a *second* browser used to prove cross-session isolation.
# The header fixtures are intentionally empty: authorization travels in the
# cookie now, so legacy call sites that still pass `headers=admin_headers` work.
# --------------------------------------------------------------------------- #


def start_workspace(client) -> int:
    """(Re)bind a client to a fresh anonymous workspace; return its project id.

    Tests that need a clean, isolated project call this on their own
    ``client`` (or the shared one) instead of creating a project through the
    API, which is intentionally forbidden in the login-free model.
    """
    client.cookies.clear()
    r = client.post("/api/session/start")
    assert r.status_code == 200, r.text
    return r.json()["project_id"]


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        start_workspace(c)
        yield c


@pytest.fixture()
def fresh_client():
    """An independent browser bound to its own empty workspace.

    Used when a test needs a project that is *not* the shared client's current
    workspace and must not disturb it (rebinding the shared client mid-module
    would break other tests that still rely on its binding).
    """
    with TestClient(app) as c:
        start_workspace(c)
        yield c


@pytest.fixture(scope="function")
def workspace(client):
    r = client.get("/api/session/current")
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture(scope="function")
def project_a(workspace):
    """The shared client's *current* workspace project.

    Function-scoped on purpose: tests are free to re-bind ``client`` to a fresh
    workspace (e.g. the pipeline tests), and this always resolves to whatever
    workspace the client is bound to right now.
    """
    return {
        "id": workspace["project_id"],
        "name": "Workspace",
        "status": "Active",
        "description": "Anonymous workspace",
        "objective": "",
    }


@pytest.fixture(scope="session")
def project_b():
    """A different browser's workspace; only used to assert 404 from `client`."""
    with TestClient(app) as other:
        r = other.post("/api/session/start")
        assert r.status_code == 200, r.text
        return {
            "id": r.json()["project_id"],
            "name": "Other Workspace",
            "status": "Active",
            "description": "",
            "objective": "",
        }


@pytest.fixture(scope="session")
def admin_headers():
    return {}


@pytest.fixture(scope="session")
def employee_headers():
    return {}


@pytest.fixture(scope="session")
def employee2_headers():
    return {}


def _actor() -> dict:
    with SessionLocal() as db:
        user = db.query(User).order_by(User.id.asc()).first()
        return {"id": user.id, "name": user.name or "Workspace User", "email": user.email}


@pytest.fixture(scope="session")
def employee(client):
    """The placeholder actor that satisfies NOT NULL user FKs."""
    return _actor()


@pytest.fixture(scope="session")
def employee2(client, employee):
    return {"id": employee["id"], "name": "Second Workspace User", "email": employee["email"]}


@pytest.fixture()
def temp_upload_dir():
    yield Path(os.environ["UPLOAD_DIR"])


__all__ = [
    "client", "fresh_client", "start_workspace", "workspace", "project_a", "project_b",
    "admin_headers", "employee_headers", "employee2_headers",
    "employee", "employee2", "temp_upload_dir",
]
