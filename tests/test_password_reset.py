"""Password reset: forgot-password + reset-password flows."""
import secrets
import pytest
from sqlalchemy.orm import Session
from tests.conftest import make_auth_client, make_client, get_csrf, _get_user_id


def _tag() -> str:
    return secrets.token_hex(5)


def _email() -> str:
    return f"reset_{_tag()}@test.com"


# ── GET /forgot-password ──────────────────────────────────────────────────────

def test_forgot_password_page_loads():
    r = make_client().get("/forgot-password")
    assert r.status_code == 200
    assert "forgot" in r.text.lower() or "пароль" in r.text.lower() or "email" in r.text.lower()


def test_forgot_password_page_redirects_if_logged_in():
    client, _, _ = make_auth_client(f"fp_redir_{_tag()}")
    r = client.get("/forgot-password", follow_redirects=False)
    assert r.status_code == 302
    assert "/swipe" in r.headers["location"]


# ── POST /forgot-password ─────────────────────────────────────────────────────

def test_forgot_password_known_email_redirects_to_sent(db):
    client, email, _ = make_auth_client(f"fp_known_{_tag()}")
    # Use a fresh unauthenticated client
    fresh = make_client()
    csrf = get_csrf(fresh)
    r = fresh.post("/forgot-password", data={"email": email, "csrftoken": csrf},
                   follow_redirects=False)
    assert r.status_code == 302
    assert "sent=1" in r.headers["location"]


def test_forgot_password_unknown_email_also_redirects(db):
    """Must not reveal whether email is registered (same response for unknown)."""
    client = make_client()
    csrf = get_csrf(client)
    r = client.post("/forgot-password",
                    data={"email": "nobody_exists@test.com", "csrftoken": csrf},
                    follow_redirects=False)
    assert r.status_code == 302
    assert "sent=1" in r.headers["location"]


def test_forgot_password_sets_token_in_db(db):
    """After posting forgot-password, the user should have a reset token in DB."""
    from app.models.models import User
    client, email, _ = make_auth_client(f"fp_token_{_tag()}")

    fresh = make_client()
    csrf = get_csrf(fresh)
    fresh.post("/forgot-password", data={"email": email, "csrftoken": csrf})

    db.expire_all()
    user = db.query(User).filter(User.email == email).first()
    assert user.password_reset_token is not None
    assert user.password_reset_expires is not None


def test_forgot_password_requires_csrf():
    client = make_client()
    r = client.post("/forgot-password", data={"email": "x@x.com"})
    assert r.status_code == 403


# ── GET /reset-password/{token} ───────────────────────────────────────────────

def _get_reset_token(db: Session, email: str) -> str:
    """Trigger forgot-password and return the DB token."""
    from app.models.models import User
    client = make_client()
    csrf = get_csrf(client)
    client.post("/forgot-password", data={"email": email, "csrftoken": csrf})
    db.expire_all()
    user = db.query(User).filter(User.email == email).first()
    return user.password_reset_token


def test_reset_password_valid_token_shows_form(db):
    _, email, _ = make_auth_client(f"rp_valid_{_tag()}")
    token = _get_reset_token(db, email)

    r = make_client().get(f"/reset-password/{token}")
    assert r.status_code == 200
    assert "invalid" not in r.text.lower() or "password" in r.text.lower()


def test_reset_password_invalid_token_shows_error():
    r = make_client().get("/reset-password/this-is-not-a-real-token")
    assert r.status_code == 400


def test_reset_password_expired_token(db):
    """Expired token should return 400."""
    from datetime import timedelta
    from app.models.models import User
    from app.utils.time import utcnow

    _, email, _ = make_auth_client(f"rp_exp_{_tag()}")
    token = _get_reset_token(db, email)

    user = db.query(User).filter(User.email == email).first()
    user.password_reset_expires = utcnow() - timedelta(hours=2)
    db.commit()

    r = make_client().get(f"/reset-password/{token}")
    assert r.status_code == 400


# ── POST /reset-password/{token} ──────────────────────────────────────────────

def test_reset_password_success_logs_in(db):
    """Valid token + valid password resets and auto-logs in → redirects to /swipe."""
    _, email, _ = make_auth_client(f"rp_ok_{_tag()}")
    token = _get_reset_token(db, email)

    fresh = make_client()
    csrf = get_csrf(fresh)
    r = fresh.post(f"/reset-password/{token}",
                   data={"password": "NewPass999!", "csrftoken": csrf},
                   follow_redirects=False)
    assert r.status_code == 302
    assert "/swipe" in r.headers["location"]
    assert "access_token" in fresh.cookies


def test_reset_password_clears_token(db):
    """After successful reset, password_reset_token must be None."""
    from app.models.models import User
    _, email, _ = make_auth_client(f"rp_clear_{_tag()}")
    token = _get_reset_token(db, email)

    fresh = make_client()
    csrf = get_csrf(fresh)
    fresh.post(f"/reset-password/{token}",
               data={"password": "NewPass999!", "csrftoken": csrf})

    db.expire_all()
    user = db.query(User).filter(User.email == email).first()
    assert user.password_reset_token is None
    assert user.password_reset_expires is None


def test_reset_password_token_version_incremented(db):
    """token_version must be incremented to invalidate old JWTs."""
    from app.models.models import User
    _, email, _ = make_auth_client(f"rp_ver_{_tag()}")

    user = db.query(User).filter(User.email == email).first()
    old_version = user.token_version or 0

    token = _get_reset_token(db, email)
    fresh = make_client()
    csrf = get_csrf(fresh)
    fresh.post(f"/reset-password/{token}",
               data={"password": "NewPass999!", "csrftoken": csrf})

    db.expire_all()
    user = db.query(User).filter(User.email == email).first()
    assert (user.token_version or 0) > old_version


def test_reset_password_old_password_no_longer_works(db):
    """After reset, the old password cannot log in."""
    _, email, _ = make_auth_client(f"rp_oldpw_{_tag()}")
    token = _get_reset_token(db, email)

    fresh = make_client()
    csrf = get_csrf(fresh)
    fresh.post(f"/reset-password/{token}",
               data={"password": "BrandNewPass1!", "csrftoken": csrf})

    # Try logging in with old password
    login_client = make_client()
    csrf2 = get_csrf(login_client)
    r = login_client.post("/login", data={
        "email": email,
        "password": "TestPass123!",  # old default password
        "csrftoken": csrf2,
    })
    assert "access_token" not in login_client.cookies


def test_reset_password_new_password_works(db):
    """After reset, the new password can log in."""
    _, email, _ = make_auth_client(f"rp_newpw_{_tag()}")
    token = _get_reset_token(db, email)

    fresh = make_client()
    csrf = get_csrf(fresh)
    fresh.post(f"/reset-password/{token}",
               data={"password": "BrandNewPass1!", "csrftoken": csrf})

    login_client = make_client()
    csrf2 = get_csrf(login_client)
    login_client.post("/login", data={
        "email": email,
        "password": "BrandNewPass1!",
        "csrftoken": csrf2,
    })
    assert "access_token" in login_client.cookies


def test_reset_password_too_short_rejected(db):
    _, email, _ = make_auth_client(f"rp_short_{_tag()}")
    token = _get_reset_token(db, email)

    fresh = make_client()
    csrf = get_csrf(fresh)
    r = fresh.post(f"/reset-password/{token}",
                   data={"password": "ab1", "csrftoken": csrf})
    assert r.status_code == 400


def test_reset_password_no_digit_rejected(db):
    _, email, _ = make_auth_client(f"rp_digit_{_tag()}")
    token = _get_reset_token(db, email)

    fresh = make_client()
    csrf = get_csrf(fresh)
    r = fresh.post(f"/reset-password/{token}",
                   data={"password": "NoDigitsHere!", "csrftoken": csrf})
    assert r.status_code == 400


def test_reset_password_token_unusable_twice(db):
    """Token is consumed on first use; second use must fail."""
    _, email, _ = make_auth_client(f"rp_twice_{_tag()}")
    token = _get_reset_token(db, email)

    c1, c2 = make_client(), make_client()
    csrf1, csrf2 = get_csrf(c1), get_csrf(c2)

    c1.post(f"/reset-password/{token}",
            data={"password": "First1Pass!", "csrftoken": csrf1})

    r2 = c2.post(f"/reset-password/{token}",
                 data={"password": "Second2Pass!", "csrftoken": csrf2})
    assert r2.status_code == 400
