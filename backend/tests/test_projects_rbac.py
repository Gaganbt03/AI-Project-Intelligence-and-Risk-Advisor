def test_employee_sees_only_assigned_projects(client, employee_headers, project_a, project_b):
    projects = client.get("/api/projects", headers=employee_headers).json()
    ids = {p["id"] for p in projects}
    assert project_a["id"] in ids
    assert project_b["id"] not in ids


def test_employee_cannot_read_other_project(client, employee_headers, project_b):
    r = client.get(f"/api/projects/{project_b['id']}", headers=employee_headers)
    assert r.status_code == 403


def test_employee_cannot_create_project(client, employee_headers):
    r = client.post(
        "/api/projects",
        headers=employee_headers,
        json={"name": "Nope", "priority": "Medium", "status": "Planning"},
    )
    assert r.status_code == 403


def test_employee_cannot_update_or_archive(client, employee_headers, admin_headers, project_b):
    assert client.put(
        f"/api/projects/{project_b['id']}", headers=employee_headers,
        json={"status": "Active"},
    ).status_code == 403
    assert client.delete(
        f"/api/projects/{project_b['id']}", headers=employee_headers
    ).status_code == 403


def test_admin_can_update_and_archive(client, admin_headers, project_a, project_b):
    r = client.put(
        f"/api/projects/{project_a['id']}", headers=admin_headers,
        json={"status": "On Hold"},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "On Hold"
    # restore
    client.put(f"/api/projects/{project_a['id']}", headers=admin_headers, json={"status": "Active"})
    arch = client.delete(f"/api/projects/{project_b['id']}", headers=admin_headers)
    assert arch.status_code == 200
    assert arch.json()["ok"] is True
    assert client.get(f"/api/projects/{project_b['id']}", headers=admin_headers).json()["status"] == "Archived"


def test_employee_membership_via_assign(client, admin_headers, project_a, project_b, employee):
    # Assign employee to the archived project (keep their original project membership).
    client.put(
        f"/api/users/{employee['id']}/projects",
        headers=admin_headers,
        json={"project_ids": [project_a["id"], project_b["id"]]},
    )
    # find the employee login token fresh
    login = client.post(
        "/api/auth/login", json={"email": employee["email"], "password": "StrongPass1!"}
    ).json()
    h = {"Authorization": f"Bearer {login['access_token']}"}
    r = client.get(f"/api/projects/{project_b['id']}", headers=h)
    assert r.status_code == 200
    assert r.json()["member_count"] >= 1