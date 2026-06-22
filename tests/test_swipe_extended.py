"""Extended swipe tests: super-like, undo, idempotency, blocked users."""
import secrets
import pytest
from tests.conftest import (
    make_auth_client, _create_profile, _get_user_id, make_client,
)


def _tag() -> str:
    return secrets.token_hex(5)


def _ip() -> str:
    return f"10.{secrets.randbelow(255)}.{secrets.randbelow(255)}.1"


def _setup_pair(db):
    """Two users with profiles. Returns (client_a, csrf_a, uid_a, client_b, csrf_b, uid_b)."""
    tag = _tag()
    client_a, email_a, csrf_a = make_auth_client(f"swx_a_{tag}")
    client_b, email_b, csrf_b = make_auth_client(f"swx_b_{tag}")
    uid_a = _get_user_id(db, email_a)
    uid_b = _get_user_id(db, email_b)
    _create_profile(db, uid_a)
    _create_profile(db, uid_b)
    return client_a, csrf_a, uid_a, client_b, csrf_b, uid_b


def _make_premium(db, email: str) -> None:
    from app.models.models import User
    user = db.query(User).filter(User.email == email).first()
    user.is_premium = True
    db.commit()


# ── Super-like ────────────────────────────────────────────────────────────────

def test_super_like_success(db):
    """is_super=1 creates a like with is_super flag set."""
    client_a, csrf_a, uid_a, _, _, uid_b = _setup_pair(db)

    r = client_a.post(
        f"/swipe/{uid_b}?action=like&is_super=1",
        headers={"x-csrf-token": csrf_a, "X-Forwarded-For": _ip()},
    )
    assert r.status_code == 200
    assert r.json().get("matched") is False

    from app.models.models import Like
    like = db.query(Like).filter(Like.liker_id == uid_a, Like.liked_id == uid_b).first()
    assert like is not None
    assert like.is_super is True


def test_super_like_non_premium_daily_limit(db):
    """Non-premium user is blocked after 5 super-likes per day (6th → 429)."""
    tag = _tag()
    client_a, email_a, csrf_a = make_auth_client(f"slimit_a_{tag}")
    uid_a = _get_user_id(db, email_a)
    _create_profile(db, uid_a)

    # Create 6 distinct targets
    targets = []
    for i in range(6):
        c, e, _ = make_auth_client(f"slimit_t{i}_{tag}")
        uid = _get_user_id(db, e)
        _create_profile(db, uid)
        targets.append(uid)

    # First 5 super-likes should succeed
    for uid_t in targets[:5]:
        r = client_a.post(
            f"/swipe/{uid_t}?action=like&is_super=1",
            headers={"x-csrf-token": csrf_a, "X-Forwarded-For": _ip()},
        )
        assert r.status_code == 200, f"Expected 200, got {r.status_code}"

    # 6th super-like must be rejected
    r = client_a.post(
        f"/swipe/{targets[5]}?action=like&is_super=1",
        headers={"x-csrf-token": csrf_a, "X-Forwarded-For": _ip()},
    )
    assert r.status_code == 429
    assert r.json().get("limit") is True


def test_super_like_premium_no_daily_limit(db):
    """Premium user is NOT limited to 5 super-likes per day."""
    tag = _tag()
    client_a, email_a, csrf_a = make_auth_client(f"sprem_a_{tag}")
    uid_a = _get_user_id(db, email_a)
    _create_profile(db, uid_a)
    _make_premium(db, email_a)

    # Create 7 targets — premium should not hit the limit
    for i in range(7):
        _, e, _ = make_auth_client(f"sprem_t{i}_{tag}")
        uid_t = _get_user_id(db, e)
        _create_profile(db, uid_t)
        r = client_a.post(
            f"/swipe/{uid_t}?action=like&is_super=1",
            headers={"x-csrf-token": csrf_a, "X-Forwarded-For": _ip()},
        )
        assert r.status_code == 200, f"Premium super-like #{i+1} failed: {r.status_code}"


# ── Undo swipe ────────────────────────────────────────────────────────────────

def test_undo_requires_premium(db):
    """Non-premium user gets 403 on /swipe/undo."""
    client_a, csrf_a, uid_a, _, _, uid_b = _setup_pair(db)

    # First do a swipe
    client_a.post(
        f"/swipe/{uid_b}?action=like",
        headers={"x-csrf-token": csrf_a, "X-Forwarded-For": _ip()},
    )

    r = client_a.post("/swipe/undo", headers={"x-csrf-token": csrf_a})
    assert r.status_code == 403


def test_undo_removes_last_like(db):
    """Premium user can undo their last swipe; like row is deleted."""
    tag = _tag()
    client_a, email_a, csrf_a = make_auth_client(f"undo_a_{tag}")
    _, email_b, _ = make_auth_client(f"undo_b_{tag}")
    uid_a = _get_user_id(db, email_a)
    uid_b = _get_user_id(db, email_b)
    _create_profile(db, uid_a)
    _create_profile(db, uid_b)
    _make_premium(db, email_a)

    client_a.post(
        f"/swipe/{uid_b}?action=like",
        headers={"x-csrf-token": csrf_a, "X-Forwarded-For": _ip()},
    )

    from app.models.models import Like
    assert db.query(Like).filter(Like.liker_id == uid_a, Like.liked_id == uid_b).first() is not None

    r = client_a.post("/swipe/undo", headers={"x-csrf-token": csrf_a})
    assert r.status_code == 200
    assert r.json().get("success") is True

    db.expire_all()
    assert db.query(Like).filter(Like.liker_id == uid_a, Like.liked_id == uid_b).first() is None


def test_undo_nothing_to_undo(db):
    """Premium user with no prior swipes gets 400."""
    tag = _tag()
    client_a, email_a, csrf_a = make_auth_client(f"undo0_a_{tag}")
    uid_a = _get_user_id(db, email_a)
    _create_profile(db, uid_a)
    _make_premium(db, email_a)

    r = client_a.post("/swipe/undo", headers={"x-csrf-token": csrf_a})
    assert r.status_code == 400


# ── Idempotency & edge cases ──────────────────────────────────────────────────

def test_like_twice_is_idempotent(db):
    """Swiping the same person twice returns 200 both times (IntegrityError handled)."""
    client_a, csrf_a, uid_a, _, _, uid_b = _setup_pair(db)

    ip = _ip()
    r1 = client_a.post(
        f"/swipe/{uid_b}?action=like",
        headers={"x-csrf-token": csrf_a, "X-Forwarded-For": ip},
    )
    assert r1.status_code == 200

    r2 = client_a.post(
        f"/swipe/{uid_b}?action=like",
        headers={"x-csrf-token": csrf_a, "X-Forwarded-For": ip},
    )
    assert r2.status_code == 200


def test_match_created_only_once(db):
    """Mutual likes create exactly one match, not two."""
    client_a, csrf_a, uid_a, client_b, csrf_b, uid_b = _setup_pair(db)

    ip_a, ip_b = _ip(), _ip()
    client_a.post(f"/swipe/{uid_b}?action=like",
                  headers={"x-csrf-token": csrf_a, "X-Forwarded-For": ip_a})
    client_b.post(f"/swipe/{uid_a}?action=like",
                  headers={"x-csrf-token": csrf_b, "X-Forwarded-For": ip_b})

    from app.models.models import Match
    from sqlalchemy import or_, and_
    matches = db.query(Match).filter(
        or_(
            and_(Match.user1_id == uid_a, Match.user2_id == uid_b),
            and_(Match.user1_id == uid_b, Match.user2_id == uid_a),
        )
    ).all()
    assert len(matches) == 1


def test_swipe_nonexistent_user_404(db):
    """Swiping a user ID that doesn't exist returns 404."""
    client_a, csrf_a, uid_a, _, _, _ = _setup_pair(db)

    r = client_a.post(
        "/swipe/999999?action=like",
        headers={"x-csrf-token": csrf_a, "X-Forwarded-For": _ip()},
    )
    assert r.status_code == 404


def test_swipe_response_includes_super_likes_left(db):
    """Response always includes super_likes_left count."""
    client_a, csrf_a, uid_a, _, _, uid_b = _setup_pair(db)

    r = client_a.post(
        f"/swipe/{uid_b}?action=like",
        headers={"x-csrf-token": csrf_a, "X-Forwarded-For": _ip()},
    )
    assert r.status_code == 200
    assert "super_likes_left" in r.json()
    assert r.json()["super_likes_left"] == 5  # no super-likes used yet
