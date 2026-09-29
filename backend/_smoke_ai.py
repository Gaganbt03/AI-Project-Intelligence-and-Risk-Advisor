import os, io, json, time
os.environ["DATABASE_URL"] = "sqlite:///./data/smoke_ai.db"
os.environ["UPLOAD_DIR"] = "./data/smoke_ai_uploads"
os.environ["VECTOR_DB_PATH"] = "./data/smoke_ai_vectors"
os.environ.pop("ADMIN_EMAIL", None)

for f in ["./data/smoke_ai.db", "./data/smoke_ai.db-wal", "./data/smoke_ai.db-shm"]:
    try: os.remove(f)
    except OSError: pass

from fastapi.testclient import TestClient
from app.main import app

DOC = b"""PROJECT ORION - WEEKLY STATUS REPORT
Project Manager: Aisha Khan
Report Date: 2026-09-20

1. EXECUTIVE SUMMARY
Project Orion is currently 2 weeks behind schedule. The planned go-live of October 15 is at risk.

2. PROGRESS
- Authentication module: 80% complete.
- Payment gateway integration: not started.
- Database migration: blocked, pending security approval from the compliance team.

3. RISKS
- The payment provider (Stripe) sandbox credentials have not been received, which delays integration testing.
- Only one backend engineer (Rahul) is available; he is also handling production support, creating a single point of failure.
- Requirements for the reporting dashboard are still unclear and undocumented.

4. BLOCKERS
- Database migration is blocked waiting on compliance approval (owner: Compliance Team, raised 2026-09-05).
- Test environment is unstable and frequently goes down.

5. ACTION ITEMS
- Rahul will complete the API integration by Friday 2026-09-25.
- Priya will prepare the test plan after integration is done.
- Aisha will follow up with compliance on the security approval.
"""

with TestClient(app) as c:
    c.post("/api/auth/setup-admin", json={"name": "Root", "email": "root@example.com", "password": "supersecret1"})
    r = c.post("/api/auth/login", json={"email": "root@example.com", "password": "supersecret1"})
    H = {"Authorization": f"Bearer {r.json()['access_token']}"}
    pid = c.post("/api/projects", headers=H, json={"name": "Orion Platform", "priority": "High"}).json()["id"]
    c.post(f"/api/documents/upload?project_id={pid}", headers=H,
           files={"file": ("Orion_Status_Report.txt", io.BytesIO(DOC), "text/plain")})
    # wait for background processing
    for _ in range(60):
        docs = c.get(f"/api/documents?project_id={pid}", headers=H).json()
        if docs and docs[0]["status"] in ("Processed", "Failed"):
            break
        time.sleep(0.5)
    print("doc status:", docs[0]["status"], "chunks:", docs[0]["chunk_count"])
    assert docs[0]["status"] == "Processed", docs

    t0 = time.time()
    r = c.post(f"/api/projects/{pid}/insights", headers=H)
    assert r.status_code == 200, r.text
    out = r.json()
    print(f"analysis took {time.time()-t0:.1f}s")
    print(json.dumps(out["agents"], indent=2))

    risks = c.get(f"/api/risks?project_id={pid}", headers=H).json()
    blockers = c.get(f"/api/blockers?project_id={pid}", headers=H).json()
    tasks = c.get(f"/api/tasks?project_id={pid}", headers=H).json()
    insights = c.get(f"/api/projects/{pid}/insights", headers=H).json()
    print("risks:", len(risks), "blockers:", len(blockers), "tasks:", len(tasks), "insights:", len(insights))
    if risks:
        print("sample risk:", risks[0]["title"], "|", risks[0]["severity"], "| src:", risks[0]["source_document"])

    # RAG assistant
    r = c.post("/api/projects/{}/assistant".format(pid), headers=H,
               json={"question": "What is blocking the database migration and who owns it?"})
    assert r.status_code == 200, r.text
    ans = r.json()
    print("assistant answer:", ans.get("answer", "")[:300])
    print("citations:", [s.get("document") for s in ans.get("sources", [])])

print("AI PIPELINE SMOKE PASSED")
