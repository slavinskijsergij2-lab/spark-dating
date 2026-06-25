"""Tests for account lockout after repeated failed logins."""
import secrets
from datetime import datetime, timedelta

from tests.conftest import make_client, get_csrf


def _email():
    return f"lock_test_{secrets.token_hex(4)}@test.com"


def _register(client, email, password="GoodPass123!"):
    csrf = get_csrf(client)
    r = client.post("/register", data={"email": email, "password": password, "csrftoken": csrf})
    return r


def _bad_login(client, email, times=1):
    for _ in range(times):
        csrf = get_csrf(client)
        r = client.post("/login", data={"email": email, "password": "WrongPass!", "csrftoken": csrf})
    return r


def _good_login(client, email, password="GoodPass123!"):
    csrf = get_csrf(client)
    return client.post(
        "/login",
        data={"email": email, "password": password, "csrftoken": csrf},
        follow_redirects=False,
    )


# ── Lockout trigger ─────────────────────────────────────────────────────────

def test_bad_login_returns_400():
    c = make_client()
    email = _email()
    _register(c, email)
    r = _bad_login(c, email)
    assert r.status_code == 400


def test_five_bad_logins_trigger_lockout():
    from tests.conftest import SessionLocal
    from app.models.models import User

    c = make_client()
    email = _email()
    _register(c, email)
    _bad_login(c, email, times=5)

    db = SessionLocal()
    try:
        u = db.query(User).filter(User.email == email).first()
        assert u.failed_logins >= 5
        assert u.locked_until is not None
        assert u.locked_until > datetime.utcnow()
    finally:
        db.close()


def test_locked_account_returns_429():
    c = make_client()
    email = _email()
    _register(c, email)
    _bad_login(c, email, times=5)
    # 6th attempt should be 429
    r = _bad_login(c, email)
    assert r.status_code == 429


def test_locked_account_response_mentions_block():
    c = make_client()
    email = _email()
    _register(c, email)
    _bad_login(c, email, times=5)
    r = _bad_login(c, email)
    assert r.status_code == 429
    # Should contain lockout message in body
    assert "заблок" in r.text.lower() or "блок" in r.text.lower() or "мин" in r.text.lower()


# ── Counter reset on success ─────────────────────────────────────────────────

def test_successful_login_resets_failed_counter():
    from tests.conftest import SessionLocal
    from app.models.models import User

    c = make_client()
    email = _email()
    _register(c, email)
    _bad_login(c, email, times=3)

    # Now log in correctly
    _good_login(c, email)

    db = SessionLocal()
    try:
        u = db.query(User).filter(User.email == email).first()
        assert u.failed_logins == 0
        assert u.locked_until is None
    finally:
        db.close()


def test_successful_login_after_partial_failures():
    c = make_client()
    email = _email()
    _register(c, email)
    _bad_login(c, email, times=4)
    # Still under limit — good login should work
    r = _good_login(c, email)
    assert r.status_code in (200, 302)


# ── Unknown email doesn't crash ──────────────────────────────────────────────

def test_wrong_email_returns_400():
    c = make_client()
    csrf = get_csrf(c)
    r = c.post("/login", data={
        "email": "nobody@nowhere.com",
        "password": "anything",
        "csrftoken": csrf,
    })
    assert r.status_code == 400


# ── Manual unlock via DB ─────────────────────────────────────────────────────

def test_expired_lockout_allows_login():
    from tests.conftest import SessionLocal
    from app.models.models import User

    c = make_client()
    email = _email()
    _register(c, email)
    _bad_login(c, email, times=5)

    # Manually expire the lockout
    db = SessionLocal()
    try:
        u = db.query(User).filter(User.email == email).first()
        u.locked_until = datetime.utcnow() - timedelta(seconds=1)
        db.commit()
    finally:
        db.close()

    # Should be allowed now
    r = _good_login(c, email)
    assert r.status_code in (200, 302)
