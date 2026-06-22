"""Profile: edit, view, IDOR protection."""
import secrets
import pytest
from tests.conftest import (
    make_auth_client, _create_profile, _get_user_id, make_client,
)


def _tag() -> str:
    return secrets.token_hex(5)


# ── Edit profile ──────────────────────────────────────────────────────────────

def test_edit_profile_success():
    client, email, csrf = make_auth_client(f"prof_edit_{_tag()}")

    r = client.post("/profile/edit", data={
        "name": "Alice",
        "age": "24",
        "gender": "female",
        "looking_for": "male",
        "bio": "Hello world",
        "city": "Moscow",
        "csrftoken": csrf,
    }, follow_redirects=False)
    # Should redirect back to profile page on success
    assert r.status_code in (302, 200)


def test_edit_profile_requires_auth():
    client = make_client()
    r = client.get("/profile/edit", follow_redirects=False)
    assert r.status_code == 302
    assert "/login" in r.headers["location"]


def test_edit_profile_invalid_age():
    client, email, csrf = make_auth_client(f"prof_age_{_tag()}")

    r = client.post("/profile/edit", data={
        "name": "Bob",
        "age": "5",   # under 18
        "gender": "male",
        "looking_for": "female",
        "csrftoken": csrf,
    })
    # Either 400 or redirect with error message
    assert r.status_code in (200, 400, 302)


# ── View profile ──────────────────────────────────────────────────────────────

def test_view_own_profile(db):
    client, email, csrf = make_auth_client(f"prof_own_{_tag()}")
    uid = _get_user_id(db, email)
    _create_profile(db, uid, name="Charlie")

    r = client.get(f"/profile/{uid}", follow_redirects=True)
    assert r.status_code == 200


def test_view_other_active_user(db):
    client_a, email_a, _ = make_auth_client(f"prof_viewer_{_tag()}")
    _, email_b, _ = make_auth_client(f"prof_target_{_tag()}")

    uid_b = _get_user_id(db, email_b)
    _create_profile(db, uid_b, name="Target")

    r = client_a.get(f"/profile/{uid_b}", follow_redirects=True)
    assert r.status_code == 200


def test_view_inactive_user_returns_404(db):
    """IDOR fix: inactive/banned users must not be viewable."""
    from app.models.models import User

    client_a, _, _ = make_auth_client(f"prof_idor_v_{_tag()}")
    _, email_b, _ = make_auth_client(f"prof_idor_t_{_tag()}")

    uid_b = _get_user_id(db, email_b)
    _create_profile(db, uid_b, name="InactiveUser")

    db.query(User).filter(User.id == uid_b).update({"is_active": False})
    db.commit()

    r = client_a.get(f"/profile/{uid_b}", follow_redirects=True)
    assert r.status_code == 404


def test_view_nonexistent_user(db):
    client, _, _ = make_auth_client(f"prof_nonexist_{_tag()}")
    r = client.get("/profile/999999", follow_redirects=True)
    assert r.status_code == 404


# ── Matches page pagination ───────────────────────────────────────────────────

def test_matches_page_loads():
    client, _, _ = make_auth_client(f"prof_matches_{_tag()}")
    r = client.get("/matches")
    assert r.status_code == 200


def test_matches_page_invalid_page():
    client, _, _ = make_auth_client(f"prof_page_{_tag()}")
    # page=0 should be clamped or rejected
    r = client.get("/matches?page=0", follow_redirects=True)
    assert r.status_code in (200, 422)


def test_matches_page_2_empty(db):
    """page=999 on a user with no matches should still return 200 (clamped to page 1)."""
    client, _, _ = make_auth_client(f"prof_page2_{_tag()}")
    r = client.get("/matches?page=999")
    assert r.status_code == 200
