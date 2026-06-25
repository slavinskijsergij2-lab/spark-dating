"""Tests for push notification subscription endpoints."""
import json
from tests.conftest import make_client, make_auth_client, get_csrf, _get_user_id, SessionLocal


def _valid_sub():
    return {
        "endpoint": "https://fcm.googleapis.com/fcm/send/fake-endpoint-xyz",
        "keys": {
            "p256dh": "BNcRdreALRFXTkOOUHK1EtK2wtaz5Ry4YfYCA_0QTpQtUbVlqHgx9sEzVVKI",
            "auth": "tBHItJI5svbpez7KI4CCXg",
        },
    }


# ── Subscribe ─────────────────────────────────────────────────────────────────

def test_subscribe_requires_auth():
    c = make_client()
    r = c.post(
        "/push/subscribe",
        json=_valid_sub(),
        follow_redirects=False,
    )
    # CSRF checked before auth: no CSRF header → 403
    assert r.status_code in (302, 401, 403)


def test_subscribe_valid_payload():
    c, _, csrf = make_auth_client("push1")
    r = c.post(
        "/push/subscribe",
        json=_valid_sub(),
        headers={"x-csrf-token": csrf},
    )
    assert r.status_code in (200, 201)


def test_subscribe_missing_endpoint_rejected():
    c, _, csrf = make_auth_client("push2")
    r = c.post(
        "/push/subscribe",
        json={"keys": {"p256dh": "abc", "auth": "xyz"}},
        headers={"x-csrf-token": csrf},
    )
    assert r.status_code in (400, 422)


def test_subscribe_missing_keys_rejected():
    c, _, csrf = make_auth_client("push3")
    r = c.post(
        "/push/subscribe",
        json={"endpoint": "https://example.com/push/fake"},
        headers={"x-csrf-token": csrf},
    )
    assert r.status_code in (400, 422)


def test_subscribe_persists_to_db():
    c, email, csrf = make_auth_client("push4")
    c.post(
        "/push/subscribe",
        json=_valid_sub(),
        headers={"x-csrf-token": csrf},
    )
    db = SessionLocal()
    try:
        uid = _get_user_id(db, email)
        from app.models.models import PushSubscription
        sub = db.query(PushSubscription).filter(PushSubscription.user_id == uid).first()
        assert sub is not None
        assert "fcm.googleapis.com" in sub.endpoint
    finally:
        db.close()


def test_subscribe_twice_upserts():
    """Subscribing twice with same endpoint should not create duplicate."""
    c, email, csrf = make_auth_client("push5")
    sub = _valid_sub()
    c.post("/push/subscribe", json=sub, headers={"x-csrf-token": csrf})
    csrf2 = get_csrf(c)
    c.post("/push/subscribe", json=sub, headers={"x-csrf-token": csrf2})

    db = SessionLocal()
    try:
        uid = _get_user_id(db, email)
        from app.models.models import PushSubscription
        count = db.query(PushSubscription).filter(PushSubscription.user_id == uid).count()
        assert count == 1
    finally:
        db.close()


# ── Unsubscribe ───────────────────────────────────────────────────────────────

def test_unsubscribe_requires_auth():
    c = make_client()
    r = c.post(
        "/push/unsubscribe",
        json={"endpoint": "https://fcm.googleapis.com/fcm/send/fake"},
        follow_redirects=False,
    )
    # CSRF checked before auth: no CSRF header → 403
    assert r.status_code in (302, 401, 403)


def test_unsubscribe_existing_sub():
    c, email, csrf = make_auth_client("push6")
    sub = _valid_sub()
    c.post("/push/subscribe", json=sub, headers={"x-csrf-token": csrf})
    csrf2 = get_csrf(c)
    r = c.post(
        "/push/unsubscribe",
        json={"endpoint": sub["endpoint"]},
        headers={"x-csrf-token": csrf2},
    )
    assert r.status_code in (200, 204)


def test_unsubscribe_nonexistent_sub_safe():
    c, _, csrf = make_auth_client("push7")
    r = c.post(
        "/push/unsubscribe",
        json={"endpoint": "https://example.com/nonexistent/push"},
        headers={"x-csrf-token": csrf},
    )
    assert r.status_code in (200, 204, 404)


def test_unsubscribe_removes_from_db():
    c, email, csrf = make_auth_client("push8")
    sub = _valid_sub()
    c.post("/push/subscribe", json=sub, headers={"x-csrf-token": csrf})

    csrf2 = get_csrf(c)
    c.post("/push/unsubscribe", json={"endpoint": sub["endpoint"]}, headers={"x-csrf-token": csrf2})

    db = SessionLocal()
    try:
        uid = _get_user_id(db, email)
        from app.models.models import PushSubscription
        sub_db = db.query(PushSubscription).filter(PushSubscription.user_id == uid).first()
        assert sub_db is None
    finally:
        db.close()
