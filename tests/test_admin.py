"""Tests for /admin panel: auth, ban/unban, photo management."""
import os
import pytest
from tests.conftest import make_client, make_auth_client, register, login, _get_user_id, _create_profile

ADMIN_KEY = "test-admin-key"


@pytest.fixture(autouse=True)
def set_admin_key(monkeypatch):
    import app.routers.admin as _admin_mod
    monkeypatch.setattr(_admin_mod, "_ADMIN_KEY", ADMIN_KEY)


def admin_client():
    c = make_client()
    c.get("/admin/login")
    c.post("/admin/login", data={"key": ADMIN_KEY}, follow_redirects=False)
    return c


# ── Login ─────────────────────────────────────────────────────────────────────

def test_admin_login_page_loads():
    c = make_client()
    r = c.get("/admin/login")
    assert r.status_code == 200
    assert "Admin" in r.text


def test_admin_login_wrong_key_stays_on_login():
    c = make_client()
    r = c.post("/admin/login", data={"key": "wrong"}, follow_redirects=True)
    assert r.status_code == 200
    assert "Admin" in r.text


def test_admin_login_valid_key_redirects():
    c = make_client()
    r = c.post("/admin/login", data={"key": ADMIN_KEY}, follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "/admin"


def test_admin_panel_without_key_forbidden():
    c = make_client()
    r = c.get("/admin")
    assert r.status_code == 403


def test_admin_panel_with_key_loads():
    c = admin_client()
    r = c.get("/admin")
    assert r.status_code == 200


def test_admin_panel_contains_user_list():
    c = admin_client()
    r = c.get("/admin")
    assert r.status_code == 200
    assert "html" in r.text.lower()


# ── Ban / Unban ───────────────────────────────────────────────────────────────

def test_admin_ban_user(db):
    c1, email1, _ = make_auth_client("ban1")
    uid = _get_user_id(db, email1)

    ac = admin_client()
    r = ac.post(f"/admin/ban/{uid}", follow_redirects=False)
    assert r.status_code == 302

    db.expire_all()
    from app.models.models import User
    u = db.query(User).filter(User.id == uid).first()
    assert u.is_active is False


def test_admin_unban_user(db):
    c1, email1, _ = make_auth_client("unban1")
    uid = _get_user_id(db, email1)

    ac = admin_client()
    ac.post(f"/admin/ban/{uid}", follow_redirects=False)
    r = ac.post(f"/admin/unban/{uid}", follow_redirects=False)
    assert r.status_code == 302

    db.expire_all()
    from app.models.models import User
    u = db.query(User).filter(User.id == uid).first()
    assert u.is_active is True


def test_admin_ban_nonexistent_user_safe():
    ac = admin_client()
    r = ac.post("/admin/ban/999999", follow_redirects=False)
    assert r.status_code == 302


def test_admin_unban_nonexistent_user_safe():
    ac = admin_client()
    r = ac.post("/admin/unban/999999", follow_redirects=False)
    assert r.status_code == 302


def test_admin_ban_without_key_forbidden():
    c = make_client()
    r = c.post("/admin/ban/1")
    assert r.status_code == 403


# ── Clear main photo ──────────────────────────────────────────────────────────

def test_admin_clear_photo_no_photo_safe(db):
    c1, email1, _ = make_auth_client("clrphoto1")
    uid = _get_user_id(db, email1)
    _create_profile(db, uid)

    ac = admin_client()
    r = ac.post(f"/admin/photo/clear/{uid}", follow_redirects=False)
    assert r.status_code == 302


def test_admin_clear_photo_removes_photo(db):
    from app.models.models import Profile
    c1, email1, _ = make_auth_client("clrphoto2")
    uid = _get_user_id(db, email1)
    _create_profile(db, uid)

    p = db.query(Profile).filter(Profile.user_id == uid).first()
    p.photo = "/photos/fake_photo.jpg"
    db.commit()

    ac = admin_client()
    ac.post(f"/admin/photo/clear/{uid}", follow_redirects=False)

    db.expire_all()
    p = db.query(Profile).filter(Profile.user_id == uid).first()
    assert p.photo is None


# ── Delete gallery photo ──────────────────────────────────────────────────────

def test_admin_delete_gallery_photo_nonexistent_safe():
    ac = admin_client()
    r = ac.post("/admin/photo/delete/999999", follow_redirects=False)
    assert r.status_code == 302


def test_admin_delete_gallery_photo_removes_record(db):
    from app.models.models import Profile, ProfilePhoto
    c1, email1, _ = make_auth_client("delgalphoto")
    uid = _get_user_id(db, email1)
    _create_profile(db, uid)

    p = db.query(Profile).filter(Profile.user_id == uid).first()
    photo = ProfilePhoto(profile_id=p.id, url="/photos/g1.jpg")
    db.add(photo)
    db.commit()
    db.refresh(photo)
    pid = photo.id

    ac = admin_client()
    ac.post(f"/admin/photo/delete/{pid}", follow_redirects=False)

    db.expire_all()
    assert db.query(ProfilePhoto).filter(ProfilePhoto.id == pid).first() is None
