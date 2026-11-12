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


def test_premium_activate(db, monkeypatch):
    import os
    monkeypatch.setenv("PREMIUM_CODES", "TEST-CODE-123")
    # Re-parse the codes in the premium router module
    import app.routers.premium as _prem
    _prem._PREMIUM_CODES = {"TEST-CODE-123"}

    client, email, csrf = make_auth_client(f"prem_act_{_tag()}")
    uid = _get_user_id(db, email)

    r = client.post(
        "/premium/activate",
        json={"code": "TEST-CODE-123"},
        headers={"x-csrf-token": csrf},
    )
    assert r.status_code == 200
    data = r.json()
    assert data.get("success") is True

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


# ── "Turn off Premium" must not destroy or orphan a purchase ─────────────────

def _grant(db, uid, **fields):
    from app.models.models import User
    user = db.get(User, uid)
    for key, value in fields.items():
        setattr(user, key, value)
    db.commit()


def _fresh_user(db, uid):
    from app.models.models import User
    db.expire_all()
    return db.get(User, uid)


def _login_again(email):
    """New session, so the 30-second user cache doesn't serve the pre-purchase user."""
    from tests.conftest import login, make_client
    client = make_client()
    return client, login(client, email)


def test_deactivate_refused_for_play_lifetime_purchase(db):
    from app.models.models import PlayPurchase
    _c, email, _ = make_auth_client(f"prem_life_{_tag()}")
    uid = _get_user_id(db, email)
    _grant(db, uid, is_premium=True)
    db.add(PlayPurchase(user_id=uid, purchase_token="tok-" + _tag(), product_id="premium_lifetime", kind="product"))
    db.commit()
    from app.auth import invalidate_user_cache
    invalidate_user_cache(uid)
    client, csrf = _login_again(email)

    r = client.post("/premium/deactivate", headers={"x-csrf-token": csrf})

    assert r.status_code == 409
    assert _fresh_user(db, uid).is_premium is True
    assert "prem-deactivate-btn" not in client.get("/premium").text


def test_deactivate_refused_while_a_subscription_is_billing(db):
    from datetime import timedelta
    from app.auth import invalidate_user_cache
    from app.models.models import PlayPurchase
    from app.utils.time import utcnow

    for kind in ("play", "stripe"):
        _c, email, _ = make_auth_client(f"prem_sub_{kind}_{_tag()}")
        uid = _get_user_id(db, email)
        until = utcnow() + timedelta(days=20)
        if kind == "play":
            _grant(db, uid, premium_until=until)
            db.add(PlayPurchase(user_id=uid, purchase_token="tok-" + _tag(), product_id="premium_monthly",
                                kind="subscription", expires_at=until))
            db.commit()
        else:
            _grant(db, uid, premium_until=until, stripe_customer_id="cus_x", stripe_subscription_id="sub_" + _tag())
        invalidate_user_cache(uid)
        client, csrf = _login_again(email)

        r = client.post("/premium/deactivate", headers={"x-csrf-token": csrf})

        assert r.status_code == 409, kind
        assert _fresh_user(db, uid).premium_until is not None, kind


def test_deactivate_still_works_for_bonus_premium_and_asks_first(db):
    from datetime import timedelta
    from app.auth import invalidate_user_cache
    from app.utils.time import utcnow
    _c, email, _ = make_auth_client(f"prem_bonus_{_tag()}")
    uid = _get_user_id(db, email)
    _grant(db, uid, premium_until=utcnow() + timedelta(days=3))
    invalidate_user_cache(uid)
    client, csrf = _login_again(email)

    page = client.get("/premium").text
    assert "prem-deactivate-btn" in page and "data-confirm=" in page

    r = client.post("/premium/deactivate", headers={"x-csrf-token": csrf})
    assert r.status_code == 200
    assert _fresh_user(db, uid).premium_until is None
