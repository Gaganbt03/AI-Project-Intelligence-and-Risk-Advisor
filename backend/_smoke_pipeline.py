"""Fast end-to-end smoke of the automatic pipeline on a throwaway database."""
import os
import tempfile
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="smoke_pipe_"))
os.environ.update(
    DATABASE_URL=f"sqlite:///{(TMP / 'smoke.db').as_posix()}",
    UPLOAD_DIR=str(TMP / "uploads"),
    VECTOR_DB_PATH=str(TMP / "vectors"),
    SECRET_KEY="smoke-secret",
    AI_TEMPERATURE="0.0",
)

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

from app.database import SessionLocal, init_db
from app.main import app

init_db()

DOC = """Project Due Date: 2026-12-31

Project goal: deliver the Smart Campus IoT platform.

Scope:
- sensor gateway firmware
- mobile application
- analytics dashboard

Deliverables:
- gateway firmware v1
- mobile app v1
- analytics dashboard v1

Risks:
- The MQTT gateway may be unreliable under load. Owner not stated.
- Sensor battery life may be insufficient. Assigned to Priya. Deadline 2026-11-20.

Blockers:
- Cloud budget approval is outstanding.

Action items:
- Priya must finalise the sensor datasheet by 2026-10-15.
- Unassigned: publish the API contract.
"""

with TestClient(app) as client:
    r = client.post("/api/auth/setup-admin", json={
        "name": "Admin", "email": "a@b.com", "password": "password123",
    })
    print("setup", r.status_code)
    tok = client.post("/api/auth/login", json={
        "email": "a@b.com", "password": "password123",
    }).json()["access_token"]
    H = {"Authorization": f"Bearer {tok}"}

    pid = client.post("/api/projects", headers=H, json={"name": "Campus"}).json()["id"]
    print("project", pid)

    files = {"file": ("spec.txt", DOC.encode(), "text/plain")}
    r = client.post(f"/api/documents/upload?project_id={pid}", headers=H, files=files)
    print("upload", r.status_code)

    db = SessionLocal()
    doc = db.query(__import__("app.models", fromlist=["ProjectDocument"]).ProjectDocument).first()
    print("doc status:", doc.status, "chunks:", doc.chunk_count)

    from app.services.documents import process_and_analyze
    try:
        process_and_analyze(db, doc.id, created_by=1)
    except Exception as exc:
        import traceback
        traceback.print_exc()
        raise SystemExit(1)

    st = client.get(f"/api/projects/{pid}/analysis/status", headers=H).json()
    print("\nstatus:", st["status"])
    for s in st["stages"]:
        print(f"  {s['status']:8} {s['label']:35} {s['detail'][:60]}")
    print("counts:", {k: v for k, v in st["counts"].items() if v})
    print("due:", st["due_date"])
    print("health:", st["health"])
    print("assistant_ready:", st["assistant_ready"])

    from app.models_m3 import AssistantConversation, AssistantMessage, GeneratedDocument
    print("\nassistant conversations:", db.query(AssistantConversation).count())
    print("assistant messages:", db.query(AssistantMessage).count())
    print("generated docs:", db.query(GeneratedDocument).count())

    gd = client.get(f"/api/projects/{pid}/documents", headers=H).json()
    print("\ngenerated:", [(d["doc_type"], d["generation_count"]) for d in gd["documents"]])

    full = client.get(f"/api/projects/{pid}/documents/{gd['documents'][0]['id']}", headers=H).json()
    print("\n--- content head ---")
    print("\n".join((full.get("content") or "").splitlines()[:12]))
    db.close()