"""Swipe mechanics: like, dislike, mutual match creation."""
import secrets
import pytest
from tests.conftest import (
    make_auth_client, _create_profile, _create_match,
    _get_user_id, make_client, get_csrf, register, login,
)


def _tag() -> str:
    return secrets.token_hex(5)


def _setup_two_users(db):
    """Register two users with profiles. Returns (client_a, csrf_a, uid_a, client_b, csrf_b, uid_b)."""
    tag = _tag()
    client_a, email_a, csrf_a = make_auth_client(f"sw_a_{tag}")
    client_b, email_b, csrf_b = make_auth_client(f"sw_b_{tag}")

    uid_a = _get_user_id(db, email_a)
    uid_b = _get_user_id(db, email_b)

    _create_profile(db, uid_a, name="Alice")
    _create_profile(db, uid_b, name="Bob")

    return client_a, csrf_a, uid_a, client_b, csrf_b, uid_b


def test_swipe_like_no_match(db):
    """Liking someone who hasn't liked back should NOT create a match."""
    client_a, csrf_a, uid_a, client_b, csrf_b, uid_b = _setup_two_users(db)

    r = client_a.post(
        f"/swipe/{uid_b}?action=like",
        headers={"x-csrf-token": csrf_a, "X-Forwarded-For": f"10.0.{secrets.token_hex(1)}.1"},
    )
    assert r.status_code == 200
    data = r.json()
    assert data.get("matched") is False


def test_swipe_dislike(db):
    """Disliking should return matched=False."""
    client_a, csrf_a, uid_a, client_b, csrf_b, uid_b = _setup_two_users(db)

    r = client_a.post(
        f"/swipe/{uid_b}?action=dislike",
        headers={"x-csrf-token": csrf_a, "X-Forwarded-For": f"10.1.{secrets.token_hex(1)}.1"},
    )
    assert r.status_code == 200
    assert r.json().get("matched") is False


def test_swipe_mutual_creates_match(db):
    """Mutual likes should create a match (matched=True on second swipe)."""
    client_a, csrf_a, uid_a, client_b, csrf_b, uid_b = _setup_two_users(db)

    ip_a = f"10.2.{secrets.token_hex(1)}.1"
    ip_b = f"10.3.{secrets.token_hex(1)}.1"

    # A likes B
    r1 = client_a.post(
        f"/swipe/{uid_b}?action=like",
        headers={"x-csrf-token": csrf_a, "X-Forwarded-For": ip_a},
    )
    assert r1.status_code == 200
    assert r1.json().get("matched") is False

    # B likes A back → should trigger match
    r2 = client_b.post(
        f"/swipe/{uid_a}?action=like",
        headers={"x-csrf-token": csrf_b, "X-Forwarded-For": ip_b},
    )
    assert r2.status_code == 200
    assert r2.json().get("matched") is True


def test_swipe_invalid_action(db):
    """Unknown action value should return 400."""
    client_a, csrf_a, uid_a, client_b, csrf_b, uid_b = _setup_two_users(db)

    r = client_a.post(
        f"/swipe/{uid_b}?action=maybe",
        headers={"x-csrf-token": csrf_a, "X-Forwarded-For": f"10.4.{secrets.token_hex(1)}.1"},
    )
    assert r.status_code == 400


def test_swipe_requires_auth():
    """Unauthenticated swipe should return 401 or redirect."""
    client = make_client()
    r = client.post("/swipe/999?action=like", headers={"x-csrf-token": "fake"},
                    follow_redirects=False)
    assert r.status_code in (302, 401, 403)
