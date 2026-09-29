import os, io, json
os.environ["DATABASE_URL"] = "sqlite:///./data/smoke_api.db"
os.environ["UPLOAD_DIR"] = "./data/smoke_uploads"
os.environ["VECTOR_DB_PATH"] = "./data/smoke_vectors"
os.environ.pop("ADMIN_EMAIL", None)

for f in ["./data/smoke_api.db", "./data/smoke_api.db-wal", "./data/smoke_api.db-shm"]:
    try: os.remove(f)
    except OSError: pass

from fastapi.testclient import TestClient
from app.main import app

with TestClient(app) as c:
    r = c.get("/api/auth/status"); assert r.json()["needs_setup"] is True, r.text
    r = c.post("/api/auth/setup-admin", json={"name": "Root Admin", "email": "root@example.com", "password": "supersecret1"})
    assert r.status_code == 201, r.text
    token = r.json()["access_token"]
    H = {"Authorization": f"Bearer {token}"}
    print("admin created, token ok")

    r = c.post("/api/projects", headers=H, json={"name": "Orion Platform", "description": "Test", "priority": "High", "status": "Active"})
    assert r.status_code == 201, r.text
    pid = r.json()["id"]
    print("project created", pid)

    # create employee
    r = c.post("/api/users", headers=H, json={"name": "Priya Sharma", "email": "priya@example.com", "password": "employee123", "role": "EMPLOYEE", "project_ids": [pid]})
    assert r.status_code == 201, r.text
    emp_id = r.json()["id"]
    print("employee created", emp_id)

    # upload txt
    files = {"file": ("Meeting_Notes.txt", io.BytesIO(b"Project Orion meeting notes.\n\nRahul will complete API integration by Friday.\nPriya will test the module after integration.\nThe database migration is blocked pending approval.\n"), "text/plain")}
    r = c.post(f"/api/documents/upload?project_id={pid}", headers=H, files=files)
    assert r.status_code == 201, r.text
    doc = r.json()
    print("uploaded", doc["original_name"], "status", doc["status"], "chunks", doc["chunk_count"])
    did = doc["id"]

    r = c.get(f"/api/documents/{did}/preview", headers=H); assert r.status_code == 200, r.text
    print("preview chars:", len(r.json()["text"]))

    r = c.get(f"/api/documents/{did}/download", headers=H); assert r.status_code == 200, r.text
    print("download bytes:", len(r.content))

    r = c.get(f"/api/documents?project_id={pid}", headers=H); assert r.status_code == 200, r.text
    print("doc list:", len(r.json()))

    # employee login + access
    r = c.post("/api/auth/login", json={"email": "priya@example.com", "password": "employee123"})
    assert r.status_code == 200, r.text
    EH = {"Authorization": f"Bearer {r.json()['access_token']}"}
    r = c.get("/api/projects", headers=EH); assert r.status_code == 200 and len(r.json()) == 1, r.text
    print("employee sees projects:", len(r.json()))

    # create second project as admin, employee must NOT access it
    r = c.post("/api/projects", headers=H, json={"name": "Hidden Project"})
    hid = r.json()["id"]
    r = c.get(f"/api/projects/{hid}", headers=EH); assert r.status_code == 403, r.text
    print("employee blocked from unassigned project: 403 OK")

    # employee reports blocker
    r = c.post(f"/api/blockers?project_id={pid}", headers=EH, json={"title": "DB migration blocked", "severity": "High", "expected_resolution": "2026-10-01"})
    assert r.status_code == 201, r.text
    print("blocker reported:", r.json()["source_type"])

    # employee cannot create users
    r = c.post("/api/users", headers=EH, json={"name": "x", "email": "x@y.com", "password": "password123", "role": "EMPLOYEE"})
    assert r.status_code == 403, r.text
    print("employee blocked from user creation: 403 OK")

    # invalid file type
    r = c.post(f"/api/documents/upload?project_id={pid}", headers=H, files={"file": ("evil.exe", io.BytesIO(b"MZ"), "application/octet-stream")})
    assert r.status_code == 415, r.text
    print("invalid file type rejected: 415 OK")

    # dashboard
    r = c.get("/api/admin/dashboard", headers=H); assert r.status_code == 200, r.text
    print("dashboard summary:", r.json()["summary"])

print("ALL API SMOKE TESTS PASSED")
