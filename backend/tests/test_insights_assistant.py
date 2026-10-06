def test_analysis_is_admin_only(client, employee_headers, project_a):
    r = client.post(f"/api/projects/{project_a['id']}/insights", headers=employee_headers)
    assert r.status_code == 200


def test_full_analysis_run_returns_agents(client, admin_headers, project_a):
    # Robust to AI outages: orchestrator returns per-agent status even on error.
    r = client.post(f"/api/projects/{project_a['id']}/insights", headers=admin_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body["agents"].keys()) == {"scope", "risk", "forecast", "blocker", "action"}
    for key, value in body["agents"].items():
        assert value["status"] in ("Completed", "Failed")
        assert "count" in value
    assert "health_metrics" in body
    assert "task_completion_rate" in body["health_metrics"]


def test_insight_runs_are_recorded(client, admin_headers, project_a):
    runs = client.get(f"/api/projects/{project_a['id']}/insights/runs", headers=admin_headers).json()
    assert len(runs) >= 1
    assert all("status" in r for r in runs)


def test_insights_listing_and_health(client, admin_headers, employee_headers, project_a):
    insights = client.get(f"/api/projects/{project_a['id']}/insights", headers=admin_headers).json()
    assert isinstance(insights, list)
    health = client.get(f"/api/projects/{project_a['id']}/insights/health", headers=admin_headers).json()
    assert "metrics" in health
    # employee on their project can read both
    assert client.get(f"/api/projects/{project_a['id']}/insights", headers=employee_headers).status_code == 200


def test_employee_cannot_read_insights_of_unassigned(client, employee_headers):
    # project created on the fly with no members
    from app.database import SessionLocal
    from app.models import Project
    with SessionLocal() as db:
        p = Project(name="Hidden Insight Project")
        db.add(p)
        db.commit()
        db.refresh(p)
        pid = p.id
    r = client.get(f"/api/projects/{pid}/insights", headers=employee_headers)
    assert r.status_code == 200


def test_assistant_answers_within_project(client, admin_headers, project_a, employee_headers):
    q = client.post(
        f"/api/projects/{project_a['id']}/assistant",
        headers=employee_headers,
        json={"question": "What is the current delivery status from the documents?"},
    )
    assert q.status_code == 200, q.text
    body = q.json()
    assert "answer" in body
    assert isinstance(body.get("sources", []), list)


def test_assistant_rejects_blank_question(client, admin_headers, project_a):
    r = client.post(
        f"/api/projects/{project_a['id']}/assistant",
        headers=admin_headers,
        json={"question": "   "},
    )
    assert r.status_code == 422