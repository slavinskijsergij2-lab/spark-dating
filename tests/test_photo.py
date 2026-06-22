"""Photo upload: profile photo, gallery management, IDOR protection."""
import io
import secrets
import pytest
from PIL import Image

from tests.conftest import make_auth_client, _create_profile, _get_user_id


def _tag() -> str:
    return secrets.token_hex(5)


def _jpeg(w: int = 100, h: int = 100) -> bytes:
    img = Image.new("RGB", (w, h), color=(200, 100, 50))
    buf = io.BytesIO()
    img.save(buf, "JPEG")
    return buf.getvalue()


def _png() -> bytes:
    img = Image.new("RGB", (100, 100), color=(50, 100, 200))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def _post_edit(client, csrf: str, photo=None, follow_redirects: bool = False):
    data = {"name": "Alice", "age": "25", "gender": "female", "csrftoken": csrf}
    files = {"photo": photo} if photo else None
    kwargs = {"data": data, "follow_redirects": follow_redirects}
    if files:
        kwargs["files"] = files
    return client.post("/profile/edit", **kwargs)


# ── Profile photo via /profile/edit ──────────────────────────────────────────

def test_upload_valid_jpeg():
    """Valid JPEG upload creates profile and redirects."""
    client, _, csrf = make_auth_client(f"phj_{_tag()}")
    r = _post_edit(client, csrf, photo=("avatar.jpg", _jpeg(), "image/jpeg"))
    assert r.status_code == 302


def test_upload_valid_png():
    """Valid PNG is accepted."""
    client, _, csrf = make_auth_client(f"php_{_tag()}")
    r = _post_edit(client, csrf, photo=("avatar.png", _png(), "image/png"))
    assert r.status_code == 302


def test_upload_photo_url_saved(db):
    """After upload, profile.photo starts with /photos/."""
    from app.models.models import Profile

    client, email, csrf = make_auth_client(f"phurl_{_tag()}")
    _post_edit(client, csrf, photo=("a.jpg", _jpeg(), "image/jpeg"))

    uid = _get_user_id(db, email)
    db.expire_all()
    profile = db.query(Profile).filter(Profile.user_id == uid).first()
    assert profile is not None
    assert profile.photo is not None
    assert profile.photo.startswith("/photos/")


def test_upload_invalid_extension():
    """.txt extension is rejected with 400."""
    client, _, csrf = make_auth_client(f"phext_{_tag()}")
    r = _post_edit(client, csrf, photo=("file.txt", b"hello world", "text/plain"))
    assert r.status_code == 400


def test_upload_not_an_image():
    """File has .jpg extension but contains garbage bytes → 400."""
    client, _, csrf = make_auth_client(f"phnoimg_{_tag()}")
    r = _post_edit(client, csrf, photo=("fake.jpg", b"\x00\x01\x02\x03 not jpeg", "image/jpeg"))
    assert r.status_code == 400


def test_upload_oversized_file():
    """File over 10 MB limit returns 400."""
    client, _, csrf = make_auth_client(f"phbig_{_tag()}")
    # Just over 10 MB — save_photo reads MAX_FILE_BYTES+1 then checks length
    big = b"\x00" * (10 * 1024 * 1024 + 1)
    r = _post_edit(client, csrf, photo=("big.jpg", big, "image/jpeg"))
    assert r.status_code == 400


# ── Gallery photos ────────────────────────────────────────────────────────────

def test_add_gallery_photo_redirects(db):
    """Uploading a gallery photo redirects back to /profile/edit."""
    client, email, csrf = make_auth_client(f"gal_redir_{_tag()}")
    _create_profile(db, _get_user_id(db, email))

    r = client.post(
        "/profile/photos/add",
        data={"csrftoken": csrf},
        files={"photo": ("g.jpg", _jpeg(), "image/jpeg")},
        follow_redirects=False,
    )
    assert r.status_code == 302
    assert "/profile/edit" in r.headers.get("location", "")


def test_add_gallery_photo_persisted(db):
    """Gallery photo URL is persisted to profile_photos table."""
    from app.models.models import Profile, ProfilePhoto

    client, email, csrf = make_auth_client(f"gal_db_{_tag()}")
    uid = _get_user_id(db, email)
    _create_profile(db, uid)

    client.post(
        "/profile/photos/add",
        data={"csrftoken": csrf},
        files={"photo": ("g.jpg", _jpeg(), "image/jpeg")},
    )

    profile = db.query(Profile).filter(Profile.user_id == uid).first()
    db.expire_all()
    photos = db.query(ProfilePhoto).filter(ProfilePhoto.profile_id == profile.id).all()
    assert len(photos) == 1
    assert photos[0].url.startswith("/photos/")


def test_add_gallery_photo_limit(db):
    """6th gallery photo redirects with ?photo_limit=1 (cap is 5)."""
    from app.models.models import Profile, ProfilePhoto

    client, email, csrf = make_auth_client(f"gal_lim_{_tag()}")
    uid = _get_user_id(db, email)
    _create_profile(db, uid)

    profile = db.query(Profile).filter(Profile.user_id == uid).first()
    for i in range(5):
        db.add(ProfilePhoto(profile_id=profile.id, url=f"/photos/fake_{i}.jpg", position=i))
    db.commit()

    r = client.post(
        "/profile/photos/add",
        data={"csrftoken": csrf},
        files={"photo": ("extra.jpg", _jpeg(), "image/jpeg")},
        follow_redirects=False,
    )
    assert r.status_code == 302
    assert "photo_limit=1" in r.headers.get("location", "")


def test_delete_gallery_photo(db):
    """Owner can delete their own gallery photo."""
    from app.models.models import Profile, ProfilePhoto

    client, email, csrf = make_auth_client(f"gal_del_{_tag()}")
    uid = _get_user_id(db, email)
    _create_profile(db, uid)

    profile = db.query(Profile).filter(Profile.user_id == uid).first()
    photo = ProfilePhoto(profile_id=profile.id, url="/photos/fake.jpg", position=0)
    db.add(photo)
    db.commit()
    db.refresh(photo)
    photo_id = photo.id

    r = client.post(
        f"/profile/photos/delete/{photo_id}",
        data={"csrftoken": csrf},
        follow_redirects=False,
    )
    assert r.status_code == 302

    db.expire_all()
    assert db.query(ProfilePhoto).filter(ProfilePhoto.id == photo_id).first() is None


def test_delete_gallery_photo_idor(db):
    """User B cannot delete User A's gallery photo (IDOR protection)."""
    from app.models.models import Profile, ProfilePhoto

    _, email_a, _ = make_auth_client(f"gal_idor_a_{_tag()}")
    client_b, _, csrf_b = make_auth_client(f"gal_idor_b_{_tag()}")

    uid_a = _get_user_id(db, email_a)
    _create_profile(db, uid_a)
    profile_a = db.query(Profile).filter(Profile.user_id == uid_a).first()
    photo = ProfilePhoto(profile_id=profile_a.id, url="/photos/a_photo.jpg", position=0)
    db.add(photo)
    db.commit()
    db.refresh(photo)
    photo_id = photo.id

    client_b.post(
        f"/profile/photos/delete/{photo_id}",
        data={"csrftoken": csrf_b},
        follow_redirects=False,
    )

    db.expire_all()
    assert db.query(ProfilePhoto).filter(ProfilePhoto.id == photo_id).first() is not None
