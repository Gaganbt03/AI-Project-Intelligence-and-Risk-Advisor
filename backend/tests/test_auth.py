import pytest


def test_setup_admin_only_once(client, admin_headers):
    # Second setup attempt must be rejected once a user exists.
    r = client.post(
        "/api/auth/setup-admin",
        json={"name": "Other", "email": "other@example.com", "password": "StrongPass1!"},
    )
    assert r.status_code == 403
    assert "already completed" in r.json()["detail"].lower()


def test_login_success_and_me(client, admin_headers):
    r = client.post("/api/auth/login", json={"email": "admin@example.com", "password": "StrongPass1!"})
    assert r.status_code == 200
    body = r.json()
    assert body["access_token"]
    assert body["user"]["email"] == "admin@example.com"

    me = client.get("/api/auth/me", headers=admin_headers)
    assert me.status_code == 200
    assert me.json()["email"] == "admin@example.com"


def test_login_wrong_password(client):
    r = client.post("/api/auth/login", json={"email": "admin@example.com", "password": "nope"})
    assert r.status_code == 401


def test_login_inactive_account_blocked(client, admin_headers, employee):
    r = client.post("/api/auth/login", json={"email": "employee@example.com", "password": "StrongPass1!"})
    assert r.status_code == 200
    uid = r.json()["user"]["id"]
    # Deactivate
    client.put(f"/api/users/{uid}", headers=admin_headers, json={"is_active": False})
    blocked = client.post("/api/auth/login", json={"email": "employee@example.com", "password": "StrongPass1!"})
    assert blocked.status_code == 403
    client.put(f"/api/users/{uid}", headers=admin_headers, json={"is_active": True})


def test_change_password_flow(client, admin_headers):
    r = client.post(
        "/api/auth/change-password",
        headers=admin_headers,
        json={"old_password": "StrongPass1!", "new_password": "RotatedPass1!"},
    )
    assert r.status_code == 200
    # Old password no longer works
    bad = client.post("/api/auth/login", json={"email": "admin@example.com", "password": "StrongPass1!"})
    assert bad.status_code == 401
    # Restore
    client.post("/api/auth/login", json={"email": "admin@example.com", "password": "RotatedPass1!"})
    r2 = client.post(
        "/api/auth/change-password",
        headers=admin_headers,
        json={"old_password": "RotatedPass1!", "new_password": "StrongPass1!"},
    )
    assert r2.status_code == 200


def test_unauthenticated_requests_rejected(client):
    assert client.get("/api/auth/me").status_code == 401
    assert client.get("/api/projects").status_code == 401


def test_password_too_short(client):
    r = client.post("/api/auth/setup-admin", json={"name": "X", "email": "x@example.com", "password": "short"})
    assert r.status_code == 422