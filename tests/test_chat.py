"""Chat: send message, retrieve messages, voice, reactions, rate limiting, access control."""
import io
import secrets
import pytest
from tests.conftest import (
    make_auth_client, _create_match, _get_user_id, make_client,
)


def _tag() -> str:
    return secrets.token_hex(5)


def _setup_match(db):
    """Create two authenticated users and a match between them.

    Returns (client_a, csrf_a, client_b, csrf_b, match_id).
    """
    tag = _tag()
    client_a, email_a, csrf_a = make_auth_client(f"chat_a_{tag}")
    client_b, email_b, csrf_b = make_auth_client(f"chat_b_{tag}")

    uid_a = _get_user_id(db, email_a)
    uid_b = _get_user_id(db, email_b)
    match_id = _create_match(db, uid_a, uid_b)

    return client_a, csrf_a, client_b, csrf_b, match_id


# ── Send message ──────────────────────────────────────────────────────────────

def test_send_message_success(db):
    client_a, csrf_a, client_b, csrf_b, mid = _setup_match(db)

    r = client_a.post(
        f"/chat/{mid}/send",
        data={"content": "Hello there!"},
        headers={"x-csrf-token": csrf_a},
    )
    assert r.status_code == 200
    body = r.json()
    assert body.get("id") is not None
    assert body["content"] == "Hello there!"


def test_send_message_empty_content(db):
    client_a, csrf_a, _, _, mid = _setup_match(db)

    r = client_a.post(
        f"/chat/{mid}/send",
        data={"content": "   "},
        headers={"x-csrf-token": csrf_a},
    )
    assert r.status_code == 400


def test_send_message_too_long(db):
    client_a, csrf_a, _, _, mid = _setup_match(db)

    r = client_a.post(
        f"/chat/{mid}/send",
        data={"content": "x" * 2001},
        headers={"x-csrf-token": csrf_a},
    )
    assert r.status_code == 400


def test_send_message_to_foreign_match(db):
    """User C should not be able to send messages into A↔B match."""
    client_a, csrf_a, client_b, csrf_b, mid = _setup_match(db)
    client_c, _, csrf_c = make_auth_client(f"chat_c_{_tag()}")

    r = client_c.post(
        f"/chat/{mid}/send",
        data={"content": "Intrude!"},
        headers={"x-csrf-token": csrf_c},
    )
    assert r.status_code in (403, 404)


def test_send_message_unauthenticated():
    client = make_client()
    r = client.post(
        "/chat/1/send",
        data={"content": "Hey"},
        headers={"x-csrf-token": "fake"},
        follow_redirects=False,
    )
    assert r.status_code in (302, 401, 403)


# ── Get messages ──────────────────────────────────────────────────────────────

def test_get_messages_returns_sent(db):
    client_a, csrf_a, client_b, csrf_b, mid = _setup_match(db)

    client_a.post(f"/chat/{mid}/send", data={"content": "msg1"},
                  headers={"x-csrf-token": csrf_a})
    client_b.post(f"/chat/{mid}/send", data={"content": "msg2"},
                  headers={"x-csrf-token": csrf_b})

    r = client_a.get(f"/chat/{mid}/messages")
    assert r.status_code == 200
    msgs = r.json()
    assert isinstance(msgs, list)
    assert len(msgs) >= 2
    contents = [m["content"] for m in msgs]
    assert "msg1" in contents
    assert "msg2" in contents


def test_get_messages_after_id(db):
    """after_id cursor should return only newer messages."""
    client_a, csrf_a, _, _, mid = _setup_match(db)

    r1 = client_a.post(f"/chat/{mid}/send", data={"content": "first"},
                       headers={"x-csrf-token": csrf_a})
    first_id = r1.json()["id"]

    client_a.post(f"/chat/{mid}/send", data={"content": "second"},
                  headers={"x-csrf-token": csrf_a})

    r = client_a.get(f"/chat/{mid}/messages?after_id={first_id}")
    msgs = r.json()
    assert all(m["id"] > first_id for m in msgs)
    assert any(m["content"] == "second" for m in msgs)


def test_get_messages_foreign_match_forbidden(db):
    """User C cannot read messages from A↔B match."""
    _, _, _, _, mid = _setup_match(db)
    client_c, _, _ = make_auth_client(f"chat_read_c_{_tag()}")

    r = client_c.get(f"/chat/{mid}/messages")
    assert r.status_code in (403, 404)
    # Should not be a list of messages
    assert not isinstance(r.json(), list)


# ── Voice messages ────────────────────────────────────────────────────────────

def test_send_voice_success(db):
    client_a, csrf_a, _, _, mid = _setup_match(db)
    fake_audio = io.BytesIO(b"RIFF\x00\x00\x00\x00WAVEfmt ")

    r = client_a.post(
        f"/chat/{mid}/voice",
        files={"audio": ("voice.webm", fake_audio, "audio/webm")},
        headers={"x-csrf-token": csrf_a},
    )
    assert r.status_code == 200
    body = r.json()
    assert body.get("id") is not None
    assert body.get("is_voice") is True
    assert body.get("sender_id") is not None
    assert body.get("created_at") is not None


def test_send_voice_returns_base64_content(db):
    client_a, csrf_a, _, _, mid = _setup_match(db)
    fake_audio = io.BytesIO(b"\x00\x01\x02\x03")

    r = client_a.post(
        f"/chat/{mid}/voice",
        files={"audio": ("voice.ogg", fake_audio, "audio/ogg")},
        headers={"x-csrf-token": csrf_a},
    )
    assert r.status_code == 200
    content = r.json().get("content", "")
    assert content.startswith("data:audio/")
    assert ";base64," in content


def test_send_voice_invalid_mime_is_normalized(db):
    """Unknown MIME type falls back to audio/webm — still succeeds."""
    client_a, csrf_a, _, _, mid = _setup_match(db)
    fake_audio = io.BytesIO(b"some audio bytes")

    r = client_a.post(
        f"/chat/{mid}/voice",
        files={"audio": ("file.bin", fake_audio, "application/octet-stream")},
        headers={"x-csrf-token": csrf_a},
    )
    assert r.status_code == 200
    assert r.json().get("is_voice") is True


def test_send_voice_foreign_match_forbidden(db):
    _, _, _, _, mid = _setup_match(db)
    client_c, _, csrf_c = make_auth_client(f"vc_c_{_tag()}")

    r = client_c.post(
        f"/chat/{mid}/voice",
        files={"audio": ("v.webm", io.BytesIO(b"data"), "audio/webm")},
        headers={"x-csrf-token": csrf_c},
    )
    assert r.status_code == 403


def test_voice_appears_in_messages_list(db):
    client_a, csrf_a, _, _, mid = _setup_match(db)

    client_a.post(
        f"/chat/{mid}/voice",
        files={"audio": ("v.webm", io.BytesIO(b"audio"), "audio/webm")},
        headers={"x-csrf-token": csrf_a},
    )

    r = client_a.get(f"/chat/{mid}/messages")
    msgs = r.json()
    voice_msgs = [m for m in msgs if m.get("is_voice")]
    assert len(voice_msgs) >= 1


# ── Reactions ─────────────────────────────────────────────────────────────────

def _send(client, mid, csrf, text="hi") -> int:
    """Send a text message and return its ID."""
    r = client.post(f"/chat/{mid}/send", data={"content": text},
                    headers={"x-csrf-token": csrf})
    assert r.status_code == 200
    return r.json()["id"]


def test_react_to_message(db):
    client_a, csrf_a, client_b, csrf_b, mid = _setup_match(db)
    msg_id = _send(client_a, mid, csrf_a)

    r = client_b.post(
        f"/chat/{mid}/message/{msg_id}/react",
        json={"emoji": "❤️"},
        headers={"x-csrf-token": csrf_b},
    )
    assert r.status_code == 200
    reactions = r.json().get("reactions", {})
    assert reactions.get("❤️") == 1


def test_react_all_allowed_emojis(db):
    client_a, csrf_a, client_b, csrf_b, mid = _setup_match(db)
    allowed = ["❤️", "😂", "😮", "😢", "👍", "🔥"]

    for emoji in allowed:
        msg_id = _send(client_a, mid, csrf_a, text=f"msg for {emoji}")
        r = client_b.post(
            f"/chat/{mid}/message/{msg_id}/react",
            json={"emoji": emoji},
            headers={"x-csrf-token": csrf_b},
        )
        assert r.status_code == 200, f"emoji {emoji} should be allowed"


def test_react_invalid_emoji_rejected(db):
    client_a, csrf_a, client_b, csrf_b, mid = _setup_match(db)
    msg_id = _send(client_a, mid, csrf_a)

    r = client_b.post(
        f"/chat/{mid}/message/{msg_id}/react",
        json={"emoji": "🤡"},
        headers={"x-csrf-token": csrf_b},
    )
    assert r.status_code == 400


def test_react_toggle_removes_reaction(db):
    """Reacting twice with the same emoji removes the reaction."""
    client_a, csrf_a, client_b, csrf_b, mid = _setup_match(db)
    msg_id = _send(client_a, mid, csrf_a)

    client_b.post(f"/chat/{mid}/message/{msg_id}/react",
                  json={"emoji": "👍"}, headers={"x-csrf-token": csrf_b})
    r = client_b.post(f"/chat/{mid}/message/{msg_id}/react",
                      json={"emoji": "👍"}, headers={"x-csrf-token": csrf_b})
    assert r.status_code == 200
    reactions = r.json().get("reactions", {})
    assert reactions.get("👍", 0) == 0


def test_react_change_emoji(db):
    """Reacting with a different emoji replaces the previous one."""
    client_a, csrf_a, client_b, csrf_b, mid = _setup_match(db)
    msg_id = _send(client_a, mid, csrf_a)

    client_b.post(f"/chat/{mid}/message/{msg_id}/react",
                  json={"emoji": "❤️"}, headers={"x-csrf-token": csrf_b})
    r = client_b.post(f"/chat/{mid}/message/{msg_id}/react",
                      json={"emoji": "😂"}, headers={"x-csrf-token": csrf_b})
    assert r.status_code == 200
    reactions = r.json()["reactions"]
    assert reactions.get("❤️", 0) == 0
    assert reactions.get("😂", 0) == 1


def test_react_foreign_match_forbidden(db):
    _, _, _, _, mid = _setup_match(db)
    client_a2, csrf_a2, _, _, mid2 = _setup_match(db)

    # Find a message in mid, try to react from client_a2 who's in mid2
    client_a, csrf_a, _, _, _ = _setup_match(db)
    # Use client_a2 against mid (they're not in it)
    r = client_a2.post(
        f"/chat/{mid}/message/9999/react",
        json={"emoji": "❤️"},
        headers={"x-csrf-token": csrf_a2},
    )
    assert r.status_code == 403


def test_react_nonexistent_message_returns_404(db):
    client_a, csrf_a, _, _, mid = _setup_match(db)

    r = client_a.post(
        f"/chat/{mid}/message/999999/react",
        json={"emoji": "❤️"},
        headers={"x-csrf-token": csrf_a},
    )
    assert r.status_code == 404


def test_reactions_appear_in_messages_endpoint(db):
    """Reactions on a message should show up in the /messages poll response."""
    client_a, csrf_a, client_b, csrf_b, mid = _setup_match(db)
    msg_id = _send(client_a, mid, csrf_a)

    client_b.post(f"/chat/{mid}/message/{msg_id}/react",
                  json={"emoji": "🔥"}, headers={"x-csrf-token": csrf_b})

    msgs = client_a.get(f"/chat/{mid}/messages").json()
    target = next((m for m in msgs if m["id"] == msg_id), None)
    assert target is not None
    assert target["reactions"].get("🔥") == 1


# ── Rate limiting ─────────────────────────────────────────────────────────────

def test_rate_limit_logic_unit():
    """Unit test for rate_limit(): verifies 429 is raised after max_calls exceeded."""
    import asyncio
    import os
    from fastapi import HTTPException
    from unittest.mock import MagicMock

    os.environ.pop("TESTING", None)
    try:
        from app.rate_limit import rate_limit

        limiter = rate_limit(3, 60)
        ip = f"unit_test_{secrets.token_hex(8)}"

        req = MagicMock()
        req.url.path = f"/test/{ip}"
        req.client.host = ip
        req.headers.get.return_value = None  # no X-Forwarded-For

        async def _run():
            for _ in range(3):
                await limiter(req)
            with pytest.raises(HTTPException) as exc_info:
                await limiter(req)
            assert exc_info.value.status_code == 429

        asyncio.run(_run())
    finally:
        os.environ["TESTING"] = "1"
