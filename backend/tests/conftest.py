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

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.database import SessionLocal, init_db  # noqa: E402
from app.services.bootstrap import ensure_roles  # noqa: E402

init_db()
with SessionLocal() as _db:
    ensure_roles(_db)


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="session")
def admin(client):
    """Creates the first administrator through the real setup-admin endpoint."""
    res = client.get("/api/auth/status")
    assert res.status_code == 200
    if res.json().get("needs_setup"):
        r = client.post(
            "/api/auth/setup-admin",
            json={"name": "Test Admin", "email": "admin@example.com", "password": "StrongPass1!"},
        )
        assert r.status_code == 201, r.text
    return client.post(
        "/api/auth/login", json={"email": "admin@example.com", "password": "StrongPass1!"}
    ).json()


@pytest.fixture(scope="session")
def admin_headers(admin):
    return {"Authorization": f"Bearer {admin['access_token']}"}


def _make_user(client, admin_headers, name, email, headers_override=None):
    r = client.post(
        "/api/users",
        headers=headers_override or admin_headers,
        json={"name": name, "email": email, "password": "StrongPass1!", "role": "EMPLOYEE", "project_ids": []},
    )
    assert r.status_code == 201, r.text
    return r.json()


@pytest.fixture(scope="session")
def employee(client, admin_headers):
    return _make_user(client, admin_headers, "Priya Employee", "employee@example.com")


@pytest.fixture(scope="session")
def employee2(client, admin_headers):
    return _make_user(client, admin_headers, "Sam Employee", "sam@example.com")


@pytest.fixture(scope="session")
def employee_headers(client, employee):
    r = client.post("/api/auth/login", json={"email": employee["email"], "password": "StrongPass1!"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture(scope="session")
def employee2_headers(client, employee2):
    r = client.post("/api/auth/login", json={"email": employee2["email"], "password": "StrongPass1!"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _make_project(client, admin_headers, name, member_ids=None):
    r = client.post(
        "/api/projects",
        headers=admin_headers,
        json={
            "name": name,
            "description": "Test project",
            "objective": "Prove the system works",
            "priority": "Medium",
            "status": "Active",
            "member_ids": member_ids or [],
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


@pytest.fixture(scope="session")
def project_a(client, admin_headers, employee, employee2):
    """Project the two employees belong to."""
    return _make_project(client, admin_headers, "Project Alpha", [employee["id"], employee2["id"]])


@pytest.fixture(scope="session")
def project_b(client, admin_headers):
    """Project NO employee belongs to (isolation test)."""
    return _make_project(client, admin_headers, "Project Bravo", [])


@pytest.fixture()
def temp_upload_dir():
    yield Path(os.environ["UPLOAD_DIR"])


__all__ = ["client", "admin", "admin_headers", "employee", "employee2", "employee_headers",
           "employee2_headers", "project_a", "project_b", "temp_upload_dir"]