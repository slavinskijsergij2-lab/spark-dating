"""Edit message: own text messages, validation, access control."""
import secrets
import pytest
from tests.conftest import make_auth_client, make_client, _create_match, _get_user_id
import io


def _tag() -> str:
    return secrets.token_hex(5)


def _setup_match(db):
    tag = _tag()
    client_a, email_a, csrf_a = make_auth_client(f"em_a_{tag}")
    client_b, email_b, csrf_b = make_auth_client(f"em_b_{tag}")
    uid_a = _get_user_id(db, email_a)
    uid_b = _get_user_id(db, email_b)
    mid = _create_match(db, uid_a, uid_b)
    return client_a, csrf_a, client_b, csrf_b, mid


def _send_msg(client, mid: int, csrf: str, text: str = "original text") -> int:
    r = client.post(f"/chat/{mid}/send", data={"content": text},
                    headers={"x-csrf-token": csrf})
    assert r.status_code == 200
    return r.json()["id"]


# ── Basic edit ────────────────────────────────────────────────────────────────

def test_edit_own_message_success(db):
    client_a, csrf_a, _, _, mid = _setup_match(db)
    msg_id = _send_msg(client_a, mid, csrf_a)

    r = client_a.post(
        f"/chat/{mid}/message/{msg_id}/edit",
        json={"content": "edited text"},
        headers={"x-csrf-token": csrf_a},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["content"] == "edited text"
    assert body["id"] == msg_id


def test_edit_sets_edited_at(db):
    """edited_at must be set after editing."""
    client_a, csrf_a, _, _, mid = _setup_match(db)
    msg_id = _send_msg(client_a, mid, csrf_a)

    r = client_a.post(
        f"/chat/{mid}/message/{msg_id}/edit",
        json={"content": "changed"},
        headers={"x-csrf-token": csrf_a},
    )
    assert r.json().get("edited_at") is not None


def test_edit_message_appears_in_messages(db):
    """Edited content must show up in the messages list."""
    client_a, csrf_a, _, _, mid = _setup_match(db)
    msg_id = _send_msg(client_a, mid, csrf_a, "before edit")

    client_a.post(
        f"/chat/{mid}/message/{msg_id}/edit",
        json={"content": "after edit"},
        headers={"x-csrf-token": csrf_a},
    )

    msgs = client_a.get(f"/chat/{mid}/messages").json()
    target = next((m for m in msgs if m["id"] == msg_id), None)
    assert target is not None
    assert target["content"] == "after edit"


def test_edit_message_db_updated(db):
    """Content and edited_at must be persisted in the database."""
    from app.models.models import Message
    client_a, csrf_a, _, _, mid = _setup_match(db)
    msg_id = _send_msg(client_a, mid, csrf_a, "original")

    client_a.post(
        f"/chat/{mid}/message/{msg_id}/edit",
        json={"content": "db updated"},
        headers={"x-csrf-token": csrf_a},
    )

    db.expire_all()
    msg = db.query(Message).filter(Message.id == msg_id).first()
    assert msg.content == "db updated"
    assert msg.edited_at is not None


def test_edit_multiple_times(db):
    """A message can be edited more than once."""
    client_a, csrf_a, _, _, mid = _setup_match(db)
    msg_id = _send_msg(client_a, mid, csrf_a, "v1")

    for version in ["v2", "v3", "final"]:
        r = client_a.post(
            f"/chat/{mid}/message/{msg_id}/edit",
            json={"content": version},
            headers={"x-csrf-token": csrf_a},
        )
        assert r.status_code == 200
        assert r.json()["content"] == version


# ── Validation ────────────────────────────────────────────────────────────────

def test_edit_empty_content_rejected(db):
    client_a, csrf_a, _, _, mid = _setup_match(db)
    msg_id = _send_msg(client_a, mid, csrf_a)

    r = client_a.post(
        f"/chat/{mid}/message/{msg_id}/edit",
        json={"content": "   "},
        headers={"x-csrf-token": csrf_a},
    )
    assert r.status_code == 400


def test_edit_too_long_content_rejected(db):
    client_a, csrf_a, _, _, mid = _setup_match(db)
    msg_id = _send_msg(client_a, mid, csrf_a)

    r = client_a.post(
        f"/chat/{mid}/message/{msg_id}/edit",
        json={"content": "x" * 2001},
        headers={"x-csrf-token": csrf_a},
    )
    assert r.status_code == 400


def test_edit_voice_message_rejected(db):
    """Voice messages cannot be edited."""
    client_a, csrf_a, _, _, mid = _setup_match(db)
    voice_r = client_a.post(
        f"/chat/{mid}/voice",
        files={"audio": ("v.webm", io.BytesIO(b"audio"), "audio/webm")},
        headers={"x-csrf-token": csrf_a},
    )
    voice_id = voice_r.json()["id"]

    r = client_a.post(
        f"/chat/{mid}/message/{voice_id}/edit",
        json={"content": "not allowed"},
        headers={"x-csrf-token": csrf_a},
    )
    assert r.status_code == 400


def test_edit_invalid_json_rejected(db):
    client_a, csrf_a, _, _, mid = _setup_match(db)
    msg_id = _send_msg(client_a, mid, csrf_a)

    r = client_a.post(
        f"/chat/{mid}/message/{msg_id}/edit",
        content=b"not json at all {{",
        headers={"x-csrf-token": csrf_a, "content-type": "application/json"},
    )
    assert r.status_code == 400


# ── Access control ────────────────────────────────────────────────────────────

def test_edit_other_users_message_forbidden(db):
    """User B cannot edit User A's message."""
    client_a, csrf_a, client_b, csrf_b, mid = _setup_match(db)
    msg_id = _send_msg(client_a, mid, csrf_a, "A's message")

    r = client_b.post(
        f"/chat/{mid}/message/{msg_id}/edit",
        json={"content": "hacked"},
        headers={"x-csrf-token": csrf_b},
    )
    assert r.status_code == 404


def test_edit_message_from_foreign_match(db):
    """User in another match cannot edit messages in this one."""
    client_a, csrf_a, _, _, mid = _setup_match(db)
    msg_id = _send_msg(client_a, mid, csrf_a)

    client_c, _, csrf_c = make_auth_client(f"em_intruder_{_tag()}")
    r = client_c.post(
        f"/chat/{mid}/message/{msg_id}/edit",
        json={"content": "intrude"},
        headers={"x-csrf-token": csrf_c},
    )
    assert r.status_code == 403


def test_edit_nonexistent_message_returns_404(db):
    client_a, csrf_a, _, _, mid = _setup_match(db)

    r = client_a.post(
        f"/chat/{mid}/message/999999/edit",
        json={"content": "ghost"},
        headers={"x-csrf-token": csrf_a},
    )
    assert r.status_code == 404


def test_edit_requires_csrf(db):
    client_a, csrf_a, _, _, mid = _setup_match(db)
    msg_id = _send_msg(client_a, mid, csrf_a)

    r = client_a.post(
        f"/chat/{mid}/message/{msg_id}/edit",
        json={"content": "no csrf"},
    )
    assert r.status_code == 403


def test_edit_unauthenticated():
    client = make_client()
    r = client.post(
        "/chat/1/message/1/edit",
        json={"content": "anon"},
        headers={"x-csrf-token": "fake"},
        follow_redirects=False,
    )
    assert r.status_code in (302, 401, 403)
