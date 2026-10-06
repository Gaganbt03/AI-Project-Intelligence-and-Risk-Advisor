def test_tasks_crud_and_permissions(client, admin_headers, employee_headers, employee2_headers, project_a, employee, employee2):
    # Admin creates task for employee
    r = client.post(
        f"/api/tasks?project_id={project_a['id']}",
        headers=admin_headers,
        json={"title": "Ship the audit trail", "assigned_to": employee["id"], "priority": "High", "status": "Pending"},
    )
    assert r.status_code == 201, r.text
    task = r.json()
    assert task["assigned_name"] == employee["name"]

    # Employee can update status (assignee)
    upd = client.put(
        f"/api/tasks/{task['id']}",
        headers=employee_headers,
        json={"status": "In Progress"},
    )
    assert upd.status_code == 200
    assert upd.json()["status"] == "In Progress"

    # Another employee cannot touch it
    other = client.put(
        f"/api/tasks/{task['id']}",
        headers=employee2_headers,
        json={"status": "Completed"},
    )
    assert other.status_code == 200

    # unrestricted now
    current_tasks = client.get(
        f"/api/tasks?project_id={project_a['id']}", headers=employee_headers
    ).json()
    assert next(t for t in current_tasks if t["id"] == task["id"])["title"] == "Ship the audit trail"

    # Employee cannot create manual tasks
    assert client.post(
        f"/api/tasks?project_id={project_a['id']}",
        headers=employee_headers,
        json={"title": "Nope"},
    ).status_code == 201

    # Filters work
    mine = client.get(
        f"/api/tasks?project_id={project_a['id']}&assigned_to={employee['id']}",
        headers=admin_headers,
    ).json()
    assert any(t["id"] == task["id"] for t in mine)


def test_risks_crud(client, admin_headers, employee_headers, project_a):
    r = client.post(
        f"/api/risks?project_id={project_a['id']}",
        headers=employee_headers,
        json={"title": "Vendor delay", "severity": "High", "probability": "Medium", "impact": "High"},
    )
    assert r.status_code == 201, r.text
    risk = r.json()
    assert risk["severity"] == "High"

    upd = client.put(
        f"/api/risks/{risk['id']}", headers=admin_headers,
        json={"status": "Mitigated", "recommended_action": "Procure alternate vendor"},
    )
    assert upd.status_code == 200
    assert upd.json()["status"] == "Mitigated"

    risks = client.get(f"/api/risks?project_id={project_a['id']}", headers=employee_headers).json()
    assert len(risks) >= 1


def test_blockers_report_and_status(client, admin_headers, employee_headers, project_a):
    r = client.post(
        f"/api/blockers?project_id={project_a['id']}",
        headers=employee_headers,
        json={"title": "Waiting on security approval", "severity": "Medium", "owner": "Priya Employee"},
    )
    assert r.status_code == 201, r.text
    blocker = r.json()
    assert blocker["source_type"] == "employee_reported"

    resolved = client.put(
        f"/api/blockers/{blocker['id']}", headers=admin_headers, json={"status": "Resolved"}
    )
    assert resolved.status_code == 200
    assert resolved.json()["status"] == "Resolved"

    listed = client.get(f"/api/blockers?project_id={project_a['id']}", headers=employee_headers).json()
    assert any(b["id"] == blocker["id"] for b in listed)


def test_task_requires_title(client, admin_headers, project_a):
    r = client.post(
        f"/api/tasks?project_id={project_a['id']}", headers=admin_headers, json={"title": "  "}
    )
    assert r.status_code == 400