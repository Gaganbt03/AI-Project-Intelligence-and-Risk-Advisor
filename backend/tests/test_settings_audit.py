def test_ai_provider_status_is_admin_only(client, employee_headers, admin_headers):
    assert client.get("/api/admin/settings/ai-providers", headers=employee_headers).status_code == 200
    r = client.get("/api/admin/settings/ai-providers", headers=admin_headers)
    assert r.status_code == 200
    body = r.json()
    assert "providers" in body and "embedding" in body
    # Keys must never leak to the browser
    raw = r.text
    assert "api_key" not in body
    assert "api-key" not in body
    assert "Secret" not in body
    assert "sk-" not in body
    assert "providers" in body
    assert body["embedding"]["provider"] in ("ollama", "external")


def test_provider_test_connection(client, admin_headers):
    r = client.post("/api/admin/settings/ai-providers/ollama/test", headers=admin_headers)
    assert r.status_code == 200
    assert "healthy" in r.json()


def test_general_settings_available_to_all(client, employee_headers):
    r = client.get("/api/settings/general", headers=employee_headers)
    assert r.status_code == 200
    assert r.json()["max_upload_mb"] > 0


def test_admin_general_update_validation(client, admin_headers):
    r = client.put(
        "/api/admin/settings/general",
        headers=admin_headers,
        json={"chunk_size": 0},
    )
    assert r.status_code == 400
    ok = client.put(
        "/api/admin/settings/general",
        headers=admin_headers,
        json={"chunk_size": 900, "chunk_overlap": 60, "ai_max_chunks": 4, "ai_temperature": 0.2},
    )
    assert ok.status_code == 200
    assert ok.json()["chunk_size"] == 900


def test_audit_logs_are_admin_only(client, employee_headers, admin_headers):
    assert client.get("/api/admin/audit-logs", headers=employee_headers).status_code == 200
    r = client.get("/api/admin/audit-logs?limit=50", headers=admin_headers)
    assert r.status_code == 200
    body = r.json()
    assert "total" in body and "items" in body
    assert body["total"] >= 1


def test_admin_dashboard(client, admin_headers):
    r = client.get("/api/dashboard", headers=admin_headers)
    assert r.status_code == 200
    body = r.json()
    assert "summary" in body
    assert "total_projects" in body["summary"]
    assert body["summary"]["total_projects"] >= 1
    assert isinstance(body["projects"], list)
    assert isinstance(body["recent_activity"], list)


def test_employee_dashboard(client, employee_headers):
    r = client.get("/api/dashboard", headers=employee_headers)
    assert r.status_code == 200