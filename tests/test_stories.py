"""Stories: create text story, create image story, feed, delete."""
import io
import secrets
import pytest
from PIL import Image
from tests.conftest import make_auth_client, _get_user_id, _create_profile


def _tag() -> str:
    return secrets.token_hex(5)


def _tiny_jpeg() -> bytes:
    """Generate a minimal valid JPEG in memory."""
    buf = io.BytesIO()
    Image.new("RGB", (10, 10), color=(255, 0, 0)).save(buf, "JPEG")
    return buf.getvalue()


# ── Page load ────────────────────────────────────────────────────────────────

def test_stories_page_loads():
    client, _, _ = make_auth_client(f"st_page_{_tag()}")
    r = client.get("/stories/page")
    assert r.status_code == 200


def test_stories_page_requires_auth():
    from tests.conftest import make_client
    r = make_client().get("/stories/page", follow_redirects=False)
    assert r.status_code == 302


# ── Create text story ─────────────────────────────────────────────────────────

def test_create_text_story():
    client, _, csrf = make_auth_client(f"st_text_{_tag()}")
    r = client.post("/stories", data={"text": "Hello world!", "csrftoken": csrf})
    assert r.status_code == 200
    assert r.json().get("success") is True
    assert r.json().get("id") is not None


def test_create_story_empty_fails():
    client, _, csrf = make_auth_client(f"st_empty_{_tag()}")
    r = client.post("/stories", data={"csrftoken": csrf})
    assert r.status_code == 400


def test_create_story_text_too_long():
    client, _, csrf = make_auth_client(f"st_long_{_tag()}")
    r = client.post("/stories", data={"text": "x" * 301, "csrftoken": csrf})
    # Text gets truncated to 300 chars — still succeeds
    assert r.status_code == 200


def test_create_story_replaces_existing():
    """Creating a second story upserts — user should always have exactly 1 active story."""
    client, _, csrf = make_auth_client(f"st_upsert_{_tag()}")
    r1 = client.post("/stories", data={"text": "First story", "csrftoken": csrf})
    id1 = r1.json()["id"]

    r2 = client.post("/stories", data={"text": "Updated story", "csrftoken": csrf})
    id2 = r2.json()["id"]

    assert id1 == id2, "Second story should reuse the same DB row"


# ── Create image story ────────────────────────────────────────────────────────

def test_create_image_story():
    client, _, csrf = make_auth_client(f"st_img_{_tag()}")
    jpeg = _tiny_jpeg()
    r = client.post(
        "/stories",
        data={"csrftoken": csrf},
        files={"photo": ("test.jpg", io.BytesIO(jpeg), "image/jpeg")},
    )
    assert r.status_code == 200
    assert r.json().get("success") is True


# ── Feed ──────────────────────────────────────────────────────────────────────

def test_stories_feed_returns_list():
    client, _, _ = make_auth_client(f"st_feed_{_tag()}")
    r = client.get("/stories/feed")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_stories_feed_includes_own_story():
    client, _, csrf = make_auth_client(f"st_own_{_tag()}")
    client.post("/stories", data={"text": "My story", "csrftoken": csrf})

    r = client.get("/stories/feed")
    feed = r.json()
    my_entries = [e for e in feed if e.get("is_me")]
    assert len(my_entries) == 1
    assert any(s["content"] == "My story" for s in my_entries[0]["stories"])


# ── Delete ────────────────────────────────────────────────────────────────────

def test_delete_own_story():
    client, _, csrf = make_auth_client(f"st_del_{_tag()}")
    r_create = client.post("/stories", data={"text": "Delete me", "csrftoken": csrf})
    story_id = r_create.json()["id"]

    r_del = client.delete(f"/stories/{story_id}", headers={"x-csrf-token": csrf})
    assert r_del.status_code == 200
    assert r_del.json().get("success") is True


def test_delete_other_users_story_is_noop(db):
    """Deleting someone else's story should silently succeed (no error, just noop)."""
    client_a, _, csrf_a = make_auth_client(f"st_del_a_{_tag()}")
    client_b, _, csrf_b = make_auth_client(f"st_del_b_{_tag()}")

    r = client_a.post("/stories", data={"text": "A's story", "csrftoken": csrf_a})
    story_id = r.json()["id"]

    # B tries to delete A's story
    r_del = client_b.delete(f"/stories/{story_id}", headers={"x-csrf-token": csrf_b})
    assert r_del.status_code == 200  # noop, not an error

    # A's story should still exist in the feed
    feed = client_a.get("/stories/feed").json()
    my_story_ids = [s["id"] for e in feed if e["is_me"] for s in e["stories"]]
    assert story_id in my_story_ids
