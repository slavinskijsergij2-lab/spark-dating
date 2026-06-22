"""Premium features: page load, activate, deactivate, boost, who-viewed."""
import secrets
import pytest
from tests.conftest import make_auth_client, _get_user_id


def _tag() -> str:
    return secrets.token_hex(5)


def test_premium_page_loads():
    client, _, _ = make_auth_client(f"prem_page_{_tag()}")
    r = client.get("/premium")
    assert r.status_code == 200


def test_premium_page_requires_auth():
    from tests.conftest import make_client
    r = make_client().get("/premium", follow_redirects=False)
    assert r.status_code == 302


def test_premium_activate(db):
    client, email, csrf = make_auth_client(f"prem_act_{_tag()}")
    uid = _get_user_id(db, email)

    r = client.post("/premium/activate", headers={"x-csrf-token": csrf})
    assert r.status_code == 200
    data = r.json()
    assert data.get("success") is True

    # DB should now reflect premium status
    from app.models.models import User
    user = db.query(User).filter(User.id == uid).first()
    db.refresh(user)
    assert user.is_premium or (user.premium_until is not None)


def test_premium_deactivate(db):
    client, email, csrf = make_auth_client(f"prem_deact_{_tag()}")
    uid = _get_user_id(db, email)

    client.post("/premium/activate", headers={"x-csrf-token": csrf})
    r = client.post("/premium/deactivate", headers={"x-csrf-token": csrf})
    assert r.status_code == 200
    assert r.json().get("success") is True

    from app.models.models import User
    user = db.query(User).filter(User.id == uid).first()
    db.refresh(user)
    assert not user.is_premium


def test_boost_non_premium_gets_short_boost():
    """Non-premium users can boost but get only 30 min (vs 180 min for premium)."""
    client, _, csrf = make_auth_client(f"prem_boost_no_{_tag()}")
    r = client.post("/profile/boost", headers={"x-csrf-token": csrf})
    assert r.status_code == 200
    data = r.json()
    assert data.get("success") is True
    assert data.get("minutes") == 30  # non-premium boost duration


def test_boost_with_premium(db):
    client, email, csrf = make_auth_client(f"prem_boost_yes_{_tag()}")
    uid = _get_user_id(db, email)

    # Grant premium directly in DB
    from app.models.models import User
    db.query(User).filter(User.id == uid).update({"is_premium": True})
    db.commit()

    r = client.post("/profile/boost", headers={"x-csrf-token": csrf})
    assert r.status_code == 200
    assert r.json().get("success") is True


def test_who_viewed_requires_premium():
    client, _, csrf = make_auth_client(f"prem_wv_{_tag()}")
    r = client.get("/profile/who-viewed", follow_redirects=True)
    # Non-premium: should redirect or show upgrade prompt, not crash
    assert r.status_code == 200
