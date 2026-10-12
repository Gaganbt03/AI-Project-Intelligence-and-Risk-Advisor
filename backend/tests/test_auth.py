"""The auth surface after the login-free workspace migration.

The product no longer has accounts or a login screen: a browser is bound to a
private workspace by an HttpOnly cookie. These tests pin the remaining auth
endpoints — first-account creation stays closed, credentials cannot be guessed,
passwords still require the current one, and anonymous workspace requests are
served without any token.
"""


def test_setup_admin_only_once(client, admin_headers):
    # Second setup attempt must be rejected once a user exists.
    r = client.post(
        "/api/auth/setup-admin",
        json={"name": "Other", "email": "other@example.com", "password": "StrongPass1!"},
    )
    assert r.status_code == 403
    assert "already completed" in r.json()["detail"].lower()


def test_auth_status_reports_no_setup_needed(client):
    assert client.get("/api/auth/status").json()["needs_setup"] is False


def test_anonymous_requests_allowed_no_login(client):
    # Login has been removed: the dashboard and API are reachable without a token.
    assert client.get("/api/auth/me").status_code == 200
    assert client.get("/api/projects").status_code == 200
    assert client.get("/api/dashboard").status_code == 200


def test_login_for_unknown_account_is_rejected(client):
    r = client.post("/api/auth/login", json={"email": "nobody@example.com", "password": "nope"})
    assert r.status_code == 401


def test_change_password_requires_the_current_password(client, admin_headers):
    r = client.post(
        "/api/auth/change-password",
        headers=admin_headers,
        json={"old_password": "definitely-wrong", "new_password": "RotatedPass1!"},
    )
    assert r.status_code == 400


def test_password_too_short(client):
    r = client.post(
        "/api/auth/setup-admin",
        json={"name": "X", "email": "x@example.com", "password": "short"},
    )
    assert r.status_code == 422
