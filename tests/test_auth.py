"""Auth flow: register, login, protected routes, error cases."""
import secrets
import pytest
from fastapi.testclient import TestClient
from tests.conftest import get_csrf, make_client, make_auth_client


def _email() -> str:
    return f"auth_{secrets.token_hex(5)}@test.com"


# ── Register ──────────────────────────────────────────────────────────────────

def test_register_success():
    client = make_client()
    csrf = get_csrf(client)
    r = client.post("/register", data={
        "email": _email(),
        "password": "SecurePass1!",
        "csrftoken": csrf,
    }, follow_redirects=False)
    # Successful registration redirects (to /profile/edit or /login)
    assert r.status_code == 302


def test_register_duplicate_email():
    client = make_client()
    email = _email()
    csrf = get_csrf(client)
    client.post("/register", data={"email": email, "password": "Pass1!", "csrftoken": csrf})

    # Second registration with same email
    csrf2 = get_csrf(client)
    r = client.post("/register", data={"email": email, "password": "Pass1!", "csrftoken": csrf2})
    # Should return 400 or show error page (not crash)
    assert r.status_code in (200, 400)
    assert "error" in r.text.lower() or "taken" in r.text.lower() or r.status_code == 400


def test_register_invalid_email():
    client = make_client()
    csrf = get_csrf(client)
    r = client.post("/register", data={
        "email": "not-an-email",
        "password": "Pass123!",
        "csrftoken": csrf,
    })
    assert r.status_code == 400


def test_register_missing_csrf():
    client = make_client()
    r = client.post("/register", data={
        "email": _email(),
        "password": "Pass123!",
        # No csrftoken field
    })
    assert r.status_code == 403


# ── Login ─────────────────────────────────────────────────────────────────────

def test_login_success():
    client, email, csrf = make_auth_client("login_ok")
    # make_auth_client already registers + logs in; just verify the cookie
    assert "access_token" in client.cookies


def test_login_wrong_password():
    # Register with one client, then try wrong password with a fresh client (no prior cookie)
    client_reg = make_client()
    email = _email()
    csrf = get_csrf(client_reg)
    client_reg.post("/register", data={"email": email, "password": "RightPass1!", "csrftoken": csrf})

    # Fresh client — no existing access_token
    client_login = make_client()
    csrf2 = get_csrf(client_login)
    r = client_login.post("/login", data={
        "email": email,
        "password": "WrongPass!",
        "csrftoken": csrf2,
    })
    assert r.status_code in (200, 400)
    assert "access_token" not in client_login.cookies


def test_login_unknown_email():
    client = make_client()
    csrf = get_csrf(client)
    r = client.post("/login", data={
        "email": "nobody_here@test.com",
        "password": "SomePass1!",
        "csrftoken": csrf,
    })
    # Should not reveal whether email exists (same response as wrong password)
    assert r.status_code in (200, 400)
    assert "access_token" not in client.cookies


# ── Protected routes ──────────────────────────────────────────────────────────

def test_protected_route_redirects_unauthenticated():
    client = make_client()
    r = client.get("/swipe", follow_redirects=False)
    assert r.status_code == 302
    assert "/login" in r.headers["location"]


def test_authenticated_user_reaches_protected_route():
    client, email, csrf = make_auth_client("auth_reach")
    r = client.get("/matches", follow_redirects=True)
    assert r.status_code == 200
