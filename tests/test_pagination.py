"""Tests for message history pagination and voice storage."""
import io
import secrets
import pytest
from tests.conftest import make_auth_client, _create_match, _get_user_id


def _tag():
    return secrets.token_hex(5)


def _setup(db):
    tag = _tag()
    client_a, email_a, csrf_a = make_auth_client(f"pg_a_{tag}")
    client_b, email_b, csrf_b = make_auth_client(f"pg_b_{tag}")
    uid_a = _get_user_id(db, email_a)
    uid_b = _get_user_id(db, email_b)
    mid = _create_match(db, uid_a, uid_b)
    return client_a, csrf_a, uid_a, client_b, csrf_b, uid_b, mid


# ── /chat/{id}/history endpoint ──────────────────────────────────────────────

def test_history_requires_auth():
    from tests.conftest import make_client
    r = make_client().get("/chat/1/history", follow_redirects=False)
    assert r.status_code in (302, 401, 403)


def test_history_returns_json(db):
    client_a, csrf_a, *_, mid = _setup(db)
    r = client_a.get(f"/chat/{mid}/history")
    assert r.status_code == 200
    body = r.json()
    assert "messages" in body
    assert "has_more" in body


def test_history_empty_chat(db):
    client_a, csrf_a, *_, mid = _setup(db)
    r = client_a.get(f"/chat/{mid}/history")
    assert r.status_code == 200
    assert r.json()["messages"] == []
    assert r.json()["has_more"] is False


def test_history_returns_existing_messages(db):
    client_a, csrf_a, *_, client_b, csrf_b, _, mid = _setup(db)
    for i in range(3):
        client_a.post(f"/chat/{mid}/send", data={"content": f"msg {i}"},
                      headers={"x-csrf-token": csrf_a})
    r = client_a.get(f"/chat/{mid}/history")
    assert len(r.json()["messages"]) == 3


def test_history_before_id_pagination(db):
    client_a, csrf_a, *_, mid = _setup(db)
    sent_ids = []
    for i in range(5):
        resp = client_a.post(f"/chat/{mid}/send", data={"content": f"m{i}"},
                             headers={"x-csrf-token": csrf_a})
        if resp.status_code == 200:
            sent_ids.append(resp.json()["id"])

    if len(sent_ids) < 3:
        pytest.skip("Not enough messages sent")

    pivot = sent_ids[2]  # 3rd message
    r = client_a.get(f"/chat/{mid}/history?before_id={pivot}")
    msgs = r.json()["messages"]
    assert all(m["id"] < pivot for m in msgs)


def test_history_forbidden_for_outsider(db):
    *_, mid = _setup(db)
    tag = _tag()
    intruder, _, _ = make_auth_client(f"pg_out_{tag}")
    r = intruder.get(f"/chat/{mid}/history")
    assert r.status_code == 403


def test_history_has_more_false_when_few_messages(db):
    client_a, csrf_a, *_, mid = _setup(db)
    client_a.post(f"/chat/{mid}/send", data={"content": "only one"},
                  headers={"x-csrf-token": csrf_a})
    r = client_a.get(f"/chat/{mid}/history")
    assert r.json()["has_more"] is False


def test_history_message_fields(db):
    client_a, csrf_a, *_, mid = _setup(db)
    client_a.post(f"/chat/{mid}/send", data={"content": "hello"},
                  headers={"x-csrf-token": csrf_a})
    r = client_a.get(f"/chat/{mid}/history")
    msg = r.json()["messages"][0]
    for field in ("id", "content", "sender_id", "created_at", "is_voice", "is_image"):
        assert field in msg, f"missing field: {field}"


# ── Voice → Volume ────────────────────────────────────────────────────────────

def test_save_audio_bytes_no_photo_dir(monkeypatch):
    """Without PHOTO_DIR, returns base64 data URL."""
    monkeypatch.delenv("PHOTO_DIR", raising=False)
    from importlib import reload
    import app.utils.audio as au
    reload(au)
    result = au.save_audio_bytes(b"hello audio", "audio/ogg")
    assert result.startswith("data:audio/ogg;base64,")


def test_save_audio_bytes_with_photo_dir(tmp_path, monkeypatch):
    """With PHOTO_DIR set, saves file and returns /photos/ URL."""
    monkeypatch.setenv("PHOTO_DIR", str(tmp_path))
    from importlib import reload
    import app.utils.audio as au
    reload(au)
    result = au.save_audio_bytes(b"hello audio", "audio/webm")
    assert result.startswith("/photos/voice_")
    assert result.endswith(".webm")
    fname = result.split("/")[-1]
    assert (tmp_path / fname).exists()


def test_save_audio_bytes_unknown_mime_defaults_to_webm(monkeypatch):
    monkeypatch.delenv("PHOTO_DIR", raising=False)
    from importlib import reload
    import app.utils.audio as au
    reload(au)
    result = au.save_audio_bytes(b"audio", "audio/unknown-format")
    assert "audio/webm" in result


def test_save_audio_bytes_all_mimes(monkeypatch, tmp_path):
    monkeypatch.setenv("PHOTO_DIR", str(tmp_path))
    from importlib import reload
    import app.utils.audio as au
    reload(au)
    for mime, ext in [("audio/webm","webm"),("audio/ogg","ogg"),
                      ("audio/mp4","m4a"),("audio/mpeg","mp3"),("audio/wav","wav")]:
        result = au.save_audio_bytes(b"x", mime)
        assert result.endswith(f".{ext}"), f"Wrong ext for {mime}: {result}"
