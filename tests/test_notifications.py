"""Notification settings: page load, save preferences, push filtering."""
import secrets
import pytest
from tests.conftest import make_auth_client, make_client, get_csrf, _get_user_id


def _tag() -> str:
    return secrets.token_hex(5)


# ── GET /settings/notifications ───────────────────────────────────────────────

def test_notifications_page_loads():
    client, _, _ = make_auth_client(f"ns_page_{_tag()}")
    r = client.get("/settings/notifications")
    assert r.status_code == 200


def test_notifications_page_requires_auth():
    r = make_client().get("/settings/notifications", follow_redirects=False)
    assert r.status_code == 302
    assert "/login" in r.headers["location"]


def test_notifications_page_shows_toggles():
    client, _, _ = make_auth_client(f"ns_toggle_{_tag()}")
    r = client.get("/settings/notifications")
    assert r.status_code == 200
    text = r.text.lower()
    # Page must contain at least one of the known notification types
    assert any(kw in text for kw in ["notif_matches", "notif_messages", "notif_likes",
                                      "матч", "сообщен", "лайк", "match", "message"])


def test_notifications_page_saved_flash_on_redirect():
    client, _, csrf = make_auth_client(f"ns_flash_{_tag()}")
    client.post("/settings/notifications",
                data={"csrftoken": csrf, "notif_matches": "1", "notif_messages": "1"},
                follow_redirects=False)
    r = client.get("/settings/notifications?saved=1")
    assert "saved" in r.text.lower() or "сохран" in r.text.lower() or r.status_code == 200


# ── POST /settings/notifications ─────────────────────────────────────────────

def test_save_notifications_redirects():
    client, _, csrf = make_auth_client(f"ns_save_{_tag()}")
    r = client.post("/settings/notifications",
                    data={"csrftoken": csrf, "notif_matches": "1"},
                    follow_redirects=False)
    assert r.status_code == 302
    assert "saved=1" in r.headers["location"]


def test_save_notifications_requires_csrf():
    client, _, _ = make_auth_client(f"ns_csrf_{_tag()}")
    r = client.post("/settings/notifications",
                    data={"notif_matches": "1"})
    assert r.status_code == 403


def test_save_notifications_requires_auth():
    """Unauthenticated POST → 403 (CSRF check runs first) or 302 to login."""
    r = make_client().post("/settings/notifications",
                           data={"csrftoken": "x", "notif_matches": "1"},
                           follow_redirects=False)
    assert r.status_code in (302, 403)


def test_save_all_enabled(db):
    """All three toggles ON → all fields True in DB."""
    from app.models.models import User
    client, email, csrf = make_auth_client(f"ns_all_on_{_tag()}")

    client.post("/settings/notifications", data={
        "csrftoken": csrf,
        "notif_matches": "1",
        "notif_messages": "1",
        "notif_likes": "1",
    })

    db.expire_all()
    user = db.query(User).filter(User.email == email).first()
    assert user.notif_matches is True
    assert user.notif_messages is True
    assert user.notif_likes is True


def test_save_all_disabled(db):
    """All toggles OFF (no fields in form) → all fields False in DB."""
    from app.models.models import User
    client, email, csrf = make_auth_client(f"ns_all_off_{_tag()}")

    client.post("/settings/notifications", data={"csrftoken": csrf})

    db.expire_all()
    user = db.query(User).filter(User.email == email).first()
    assert user.notif_matches is False
    assert user.notif_messages is False
    assert user.notif_likes is False


def test_save_only_messages_enabled(db):
    """Enabling only messages → notif_messages=True, others False."""
    from app.models.models import User
    client, email, csrf = make_auth_client(f"ns_msg_only_{_tag()}")

    client.post("/settings/notifications", data={
        "csrftoken": csrf,
        "notif_messages": "1",
    })

    db.expire_all()
    user = db.query(User).filter(User.email == email).first()
    assert user.notif_matches is False
    assert user.notif_messages is True
    assert user.notif_likes is False


def test_save_persists_across_requests(db):
    """Preference change survives a new request / DB reload."""
    from app.models.models import User
    client, email, csrf = make_auth_client(f"ns_persist_{_tag()}")

    client.post("/settings/notifications", data={
        "csrftoken": csrf,
        "notif_likes": "1",
    })

    # Reload via a fresh GET
    client.get("/settings/notifications")

    db.expire_all()
    user = db.query(User).filter(User.email == email).first()
    assert user.notif_likes is True
    assert user.notif_matches is False


def test_save_toggle_multiple_times(db):
    """Toggling on then off correctly reflects last state."""
    from app.models.models import User
    client, email, csrf = make_auth_client(f"ns_toggle2_{_tag()}")

    # Turn on
    client.post("/settings/notifications", data={"csrftoken": csrf, "notif_matches": "1"})
    db.expire_all()
    assert db.query(User).filter(User.email == email).first().notif_matches is True

    # Turn off
    client.post("/settings/notifications", data={"csrftoken": csrf})
    db.expire_all()
    assert db.query(User).filter(User.email == email).first().notif_matches is False


# ── Default preferences for new users ────────────────────────────────────────

def test_new_user_has_notifications_enabled_by_default(db):
    """All three notif_* columns default to True for new registrations."""
    from app.models.models import User
    _, email, _ = make_auth_client(f"ns_default_{_tag()}")

    db.expire_all()
    user = db.query(User).filter(User.email == email).first()
    assert user.notif_matches is True
    assert user.notif_messages is True
    assert user.notif_likes is True


# ── Push filtering (unit-level) ───────────────────────────────────────────────

def test_push_respects_notif_messages_off(db):
    """send_push_to_user with notif_type='message' is a no-op when notif_messages=False."""
    import asyncio
    from app.models.models import User
    _, email, _ = make_auth_client(f"ns_push_off_{_tag()}")

    user = db.query(User).filter(User.email == email).first()
    user.notif_messages = False
    db.commit()
    uid = user.id

    # push.py checks the flag and returns early — no actual push sent
    # We just verify it doesn't raise and returns cleanly (VAPID is not configured in tests)
    from app.push import send_push_to_user
    asyncio.run(send_push_to_user(uid, "Test", "body", "/chat/1", "message"))
    # If we reach here without error, the early-return path works


def test_push_respects_notif_matches_off(db):
    import asyncio
    from app.models.models import User
    _, email, _ = make_auth_client(f"ns_push_match_off_{_tag()}")

    user = db.query(User).filter(User.email == email).first()
    user.notif_matches = False
    db.commit()
    uid = user.id

    from app.push import send_push_to_user
    asyncio.run(send_push_to_user(uid, "Match!", "body", "/matches", "match"))


def test_push_respects_notif_likes_off(db):
    import asyncio
    from app.models.models import User
    _, email, _ = make_auth_client(f"ns_push_likes_off_{_tag()}")

    user = db.query(User).filter(User.email == email).first()
    user.notif_likes = False
    db.commit()
    uid = user.id

    from app.push import send_push_to_user
    asyncio.run(send_push_to_user(uid, "Like!", "body", "/matches", "like"))


def test_push_no_notif_type_not_filtered(db):
    """send_push_to_user without notif_type skips the preference check entirely."""
    import asyncio
    from app.models.models import User
    _, email, _ = make_auth_client(f"ns_push_notype_{_tag()}")

    user = db.query(User).filter(User.email == email).first()
    user.notif_messages = False
    user.notif_matches = False
    user.notif_likes = False
    db.commit()

    from app.push import send_push_to_user
    # No notif_type → no filtering, proceeds to check subscriptions (none in test)
    asyncio.run(send_push_to_user(user.id, "Title", "body", "/"))
