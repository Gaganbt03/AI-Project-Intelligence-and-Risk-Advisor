"""Workspace isolation (replaces the removed role-based RBAC).

Login is gone: every browser is bound to exactly one private workspace through
an HttpOnly cookie and can only ever read or change that workspace's project.
A project outside the caller's workspace is reported as 404, never 403, so the
existence of another workspace is not disclosed.
"""


def test_workspace_sees_only_its_own_project(client, employee_headers, project_a):
    projects = client.get("/api/projects", headers=employee_headers).json()
    ids = {p["id"] for p in projects}
    assert ids == {project_a["id"]}


def test_workspace_cannot_read_other_project(client, employee_headers, project_b):
    r = client.get(f"/api/projects/{project_b['id']}", headers=employee_headers)
    assert r.status_code == 404


def test_project_creation_is_not_available(client, employee_headers):
    r = client.post(
        "/api/projects",
        headers=employee_headers,
        json={"name": "Nope", "priority": "Medium", "status": "Planning"},
    )
    assert r.status_code == 403


def test_workspace_cannot_update_or_archive_another_project(
    client, employee_headers, admin_headers, project_b
):
    assert client.put(
        f"/api/projects/{project_b['id']}", headers=employee_headers,
        json={"status": "Active"},
    ).status_code == 404
    assert client.delete(
        f"/api/projects/{project_b['id']}", headers=employee_headers
    ).status_code == 404


def test_workspace_can_update_and_archive_its_own_project(client, admin_headers, project_a):
    r = client.put(
        f"/api/projects/{project_a['id']}", headers=admin_headers,
        json={"status": "On Hold"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "On Hold"

    arch = client.delete(f"/api/projects/{project_a['id']}", headers=admin_headers)
    assert arch.status_code == 200, arch.text
    assert arch.json()["ok"] is True
    assert client.get(
        f"/api/projects/{project_a['id']}", headers=admin_headers
    ).json()["status"] == "Archived"
