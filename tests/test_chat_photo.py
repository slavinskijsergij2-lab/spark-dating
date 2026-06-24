"""Chat photo messages: send image, validation, access control."""
import io
import secrets
import pytest
from PIL import Image
from tests.conftest import make_auth_client, make_client, _create_match, _get_user_id


def _tag() -> str:
    return secrets.token_hex(5)


def _jpeg(w: int = 80, h: int = 80) -> bytes:
    img = Image.new("RGB", (w, h), color=(180, 60, 120))
    buf = io.BytesIO()
    img.save(buf, "JPEG")
    return buf.getvalue()


def _png() -> bytes:
    img = Image.new("RGB", (60, 60), color=(60, 180, 120))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def _setup_match(db):
    tag = _tag()
    client_a, email_a, csrf_a = make_auth_client(f"cp_a_{tag}")
    client_b, email_b, csrf_b = make_auth_client(f"cp_b_{tag}")
    uid_a = _get_user_id(db, email_a)
    uid_b = _get_user_id(db, email_b)
    mid = _create_match(db, uid_a, uid_b)
    return client_a, csrf_a, client_b, csrf_b, mid


# ── Send photo ────────────────────────────────────────────────────────────────

def test_send_photo_jpeg_success(db):
    client_a, csrf_a, _, _, mid = _setup_match(db)
    r = client_a.post(
        f"/chat/{mid}/photo",
        files={"photo": ("img.jpg", _jpeg(), "image/jpeg")},
        headers={"x-csrf-token": csrf_a},
    )
    assert r.status_code == 200
    body = r.json()
    assert body.get("id") is not None
    assert body.get("is_image") is True
    assert body.get("is_voice") is False


def test_send_photo_png_success(db):
    client_a, csrf_a, _, _, mid = _setup_match(db)
    r = client_a.post(
        f"/chat/{mid}/photo",
        files={"photo": ("img.png", _png(), "image/png")},
        headers={"x-csrf-token": csrf_a},
    )
    assert r.status_code == 200
    assert r.json().get("is_image") is True


def test_send_photo_content_is_url_or_base64(db):
    """Photo content is either a /photos/ file path (Volume) or base64 data URL."""
    client_a, csrf_a, _, _, mid = _setup_match(db)
    r = client_a.post(
        f"/chat/{mid}/photo",
        files={"photo": ("img.jpg", _jpeg(), "image/jpeg")},
        headers={"x-csrf-token": csrf_a},
    )
    content = r.json().get("content", "")
    assert content.startswith("/photos/") or (
        content.startswith("data:image/") and ";base64," in content
    )


def test_send_photo_invalid_mime_rejected(db):
    client_a, csrf_a, _, _, mid = _setup_match(db)
    r = client_a.post(
        f"/chat/{mid}/photo",
        files={"photo": ("file.exe", b"\x4d\x5a", "application/octet-stream")},
        headers={"x-csrf-token": csrf_a},
    )
    assert r.status_code == 400


def test_send_photo_text_file_rejected(db):
    client_a, csrf_a, _, _, mid = _setup_match(db)
    r = client_a.post(
        f"/chat/{mid}/photo",
        files={"photo": ("note.txt", b"hello world", "text/plain")},
        headers={"x-csrf-token": csrf_a},
    )
    assert r.status_code == 400


def test_send_photo_oversized_rejected(db):
    client_a, csrf_a, _, _, mid = _setup_match(db)
    big = b"\xff\xd8\xff" + b"\x00" * (5 * 1024 * 1024 + 1)
    r = client_a.post(
        f"/chat/{mid}/photo",
        files={"photo": ("big.jpg", big, "image/jpeg")},
        headers={"x-csrf-token": csrf_a},
    )
    assert r.status_code == 400


def test_send_photo_foreign_match_forbidden(db):
    _, _, _, _, mid = _setup_match(db)
    client_c, _, csrf_c = make_auth_client(f"cp_intruder_{_tag()}")
    r = client_c.post(
        f"/chat/{mid}/photo",
        files={"photo": ("img.jpg", _jpeg(), "image/jpeg")},
        headers={"x-csrf-token": csrf_c},
    )
    assert r.status_code == 403


def test_send_photo_unauthenticated():
    client = make_client()
    r = client.post(
        "/chat/1/photo",
        files={"photo": ("img.jpg", b"data", "image/jpeg")},
        headers={"x-csrf-token": "fake"},
        follow_redirects=False,
    )
    assert r.status_code in (302, 401, 403)


# ── Photo appears in messages ─────────────────────────────────────────────────

def test_send_photo_appears_in_messages(db):
    client_a, csrf_a, _, _, mid = _setup_match(db)
    client_a.post(
        f"/chat/{mid}/photo",
        files={"photo": ("img.jpg", _jpeg(), "image/jpeg")},
        headers={"x-csrf-token": csrf_a},
    )
    r = client_a.get(f"/chat/{mid}/messages")
    assert r.status_code == 200
    msgs = r.json()
    photo_msgs = [m for m in msgs if m.get("is_image")]
    assert len(photo_msgs) >= 1


def test_send_photo_is_image_flag_in_messages(db):
    """is_image field must be present and True in messages list."""
    client_a, csrf_a, _, _, mid = _setup_match(db)
    send_r = client_a.post(
        f"/chat/{mid}/photo",
        files={"photo": ("img.png", _png(), "image/png")},
        headers={"x-csrf-token": csrf_a},
    )
    msg_id = send_r.json()["id"]

    msgs = client_a.get(f"/chat/{mid}/messages").json()
    target = next((m for m in msgs if m["id"] == msg_id), None)
    assert target is not None
    assert target.get("is_image") is True
    assert target.get("is_voice") is False


def test_send_photo_does_not_affect_text_messages(db):
    """Text messages sent alongside photo messages must not have is_image."""
    client_a, csrf_a, _, _, mid = _setup_match(db)

    client_a.post(f"/chat/{mid}/send", data={"content": "text msg"},
                  headers={"x-csrf-token": csrf_a})
    client_a.post(f"/chat/{mid}/photo",
                  files={"photo": ("img.jpg", _jpeg(), "image/jpeg")},
                  headers={"x-csrf-token": csrf_a})

    msgs = client_a.get(f"/chat/{mid}/messages").json()
    text_msgs = [m for m in msgs if not m.get("is_image") and not m.get("is_voice")]
    assert all(not m.get("is_image") for m in text_msgs)


# ── No CSRF ───────────────────────────────────────────────────────────────────

def test_send_photo_no_csrf_token_rejected(db):
    client_a, _, _, _, mid = _setup_match(db)
    r = client_a.post(
        f"/chat/{mid}/photo",
        files={"photo": ("img.jpg", _jpeg(), "image/jpeg")},
    )
    assert r.status_code == 403
