"""Tests for Google Play Billing: /billing/play/verify and the RTDN push endpoint (Play API mocked)."""
import base64
import json
from datetime import timedelta

import pytest

from app import play_billing
from app.models.models import PlayPurchase, User
from app.routers import billing
from app.utils.time import utcnow
from tests.conftest import _get_user_id, make_auth_client, make_client

SKU_SUB = "premium_monthly"
SKU_LIFE = "premium_lifetime"


def _rfc3339(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%S.123456789Z")


def _sub(state="SUBSCRIPTION_STATE_ACTIVE", days=30, ack="ACKNOWLEDGEMENT_STATE_PENDING"):
    return {
        "subscriptionState": state,
        "acknowledgementState": ack,
        "lineItems": [{"productId": SKU_SUB, "expiryTime": _rfc3339(utcnow() + timedelta(days=days))}],
    }


@pytest.fixture()
def play(monkeypatch):
    """Enable Play billing and route Play API calls to an in-memory fake."""
    state = {"subs": {}, "products": {}, "acked": []}
    monkeypatch.setattr(play_billing, "play_enabled", True)
    monkeypatch.setattr(play_billing, "PACKAGE_NAME", "club.test.app")
    monkeypatch.setattr(play_billing, "SKU_MONTHLY", SKU_SUB)
    monkeypatch.setattr(play_billing, "SKU_LIFETIME", SKU_LIFE)
    monkeypatch.setattr(billing, "_PLAY_RTDN_TOKEN", "rtdn-secret")

    async def get_subscription(token):
        if token not in state["subs"]:
            raise play_billing.PurchaseNotFound("404")
        return state["subs"][token]

    async def get_product(product_id, token):
        if token not in state["products"]:
            raise play_billing.PurchaseNotFound("404")
        return state["products"][token]

    async def ack_sub(product_id, token):
        state["acked"].append(token)

    async def ack_product(product_id, token):
        state["acked"].append(token)

    monkeypatch.setattr(play_billing, "get_subscription", get_subscription)
    monkeypatch.setattr(play_billing, "get_product", get_product)
    monkeypatch.setattr(play_billing, "acknowledge_subscription", ack_sub)
    monkeypatch.setattr(play_billing, "acknowledge_product", ack_product)
    return state


def _verify(client, csrf, sku, token):
    return client.post(
        "/billing/play/verify",
        json={"product_id": sku, "purchase_token": token},
        headers={"x-csrf-token": csrf},
    )


def _rtdn(payload, token="rtdn-secret"):
    data = base64.b64encode(json.dumps(payload).encode()).decode()
    return make_client().post(f"/billing/play/rtdn?token={token}", json={"message": {"data": data}})


def _user(db, email):
    db.expire_all()
    return db.get(User, _get_user_id(db, email))


# ── verify ────────────────────────────────────────────────────────────────────

def test_verify_disabled_returns_400():
    c, _, csrf = make_auth_client("play_off")
    assert _verify(c, csrf, SKU_SUB, "tok").status_code == 400


def test_verify_requires_csrf(play):
    c, _, _ = make_auth_client("play_csrf")
    r = c.post("/billing/play/verify", json={"product_id": SKU_SUB, "purchase_token": "t"})
    assert r.status_code in (400, 403)


def test_verify_subscription_grants_premium_and_acknowledges(play, db):
    c, email, csrf = make_auth_client("play_sub")
    play["subs"]["sub-tok-1"] = _sub()
    r = _verify(c, csrf, SKU_SUB, "sub-tok-1")
    assert r.status_code == 200, r.text
    u = _user(db, email)
    assert u.is_premium_active and not u.is_premium
    assert u.premium_until > utcnow() + timedelta(days=29)
    assert play["acked"] == ["sub-tok-1"]
    assert db.query(PlayPurchase).filter_by(purchase_token="sub-tok-1").one().user_id == u.id


def test_verify_lifetime_grants_permanent_premium(play, db):
    c, email, csrf = make_auth_client("play_life")
    play["products"]["life-tok-1"] = {"purchaseState": 0, "acknowledgementState": 0}
    assert _verify(c, csrf, SKU_LIFE, "life-tok-1").status_code == 200
    assert _user(db, email).is_premium
    assert play["acked"] == ["life-tok-1"]


def test_verify_already_acknowledged_is_not_acked_again(play):
    c, _, csrf = make_auth_client("play_acked")
    play["subs"]["sub-tok-acked"] = _sub(ack="ACKNOWLEDGEMENT_STATE_ACKNOWLEDGED")
    assert _verify(c, csrf, SKU_SUB, "sub-tok-acked").status_code == 200
    assert play["acked"] == []


def test_verify_expired_subscription_rejected(play, db):
    c, email, csrf = make_auth_client("play_exp")
    play["subs"]["sub-tok-exp"] = _sub(state="SUBSCRIPTION_STATE_EXPIRED", days=-1)
    assert _verify(c, csrf, SKU_SUB, "sub-tok-exp").status_code == 400
    assert not _user(db, email).is_premium_active


def test_verify_pending_product_rejected(play):
    c, _, csrf = make_auth_client("play_pend")
    play["products"]["life-tok-pend"] = {"purchaseState": 2, "acknowledgementState": 0}
    assert _verify(c, csrf, SKU_LIFE, "life-tok-pend").status_code == 402


def test_verify_unknown_token_rejected(play):
    c, _, csrf = make_auth_client("play_unknown")
    assert _verify(c, csrf, SKU_SUB, "nope").status_code == 400


def test_verify_unknown_product_rejected(play):
    c, _, csrf = make_auth_client("play_badsku")
    assert _verify(c, csrf, "something_else", "tok").status_code == 400


def test_token_cannot_be_reused_by_another_account(play, db):
    c1, _, csrf1 = make_auth_client("play_owner")
    c2, email2, csrf2 = make_auth_client("play_thief")
    play["subs"]["sub-tok-shared"] = _sub()
    assert _verify(c1, csrf1, SKU_SUB, "sub-tok-shared").status_code == 200
    assert _verify(c2, csrf2, SKU_SUB, "sub-tok-shared").status_code == 409
    assert not _user(db, email2).is_premium_active


def test_reverify_same_token_is_idempotent(play, db):
    c, _, csrf = make_auth_client("play_again")
    play["subs"]["sub-tok-again"] = _sub()
    assert _verify(c, csrf, SKU_SUB, "sub-tok-again").status_code == 200
    assert _verify(c, csrf, SKU_SUB, "sub-tok-again").status_code == 200
    assert db.query(PlayPurchase).filter_by(purchase_token="sub-tok-again").count() == 1


# ── RTDN ──────────────────────────────────────────────────────────────────────

def test_rtdn_rejects_bad_token(play):
    assert _rtdn({"packageName": "club.test.app"}, token="wrong").status_code == 403


def test_rtdn_renewal_extends_and_expiry_revokes(play, db):
    c, email, csrf = make_auth_client("play_rtdn")
    play["subs"]["sub-tok-rtdn"] = _sub(days=3)
    assert _verify(c, csrf, SKU_SUB, "sub-tok-rtdn").status_code == 200

    play["subs"]["sub-tok-rtdn"] = _sub(days=33)
    note = {"packageName": "club.test.app",
            "subscriptionNotification": {"notificationType": 2, "purchaseToken": "sub-tok-rtdn"}}
    assert _rtdn(note).status_code == 200
    assert _user(db, email).premium_until > utcnow() + timedelta(days=32)

    play["subs"]["sub-tok-rtdn"] = _sub(state="SUBSCRIPTION_STATE_EXPIRED", days=-1)
    assert _rtdn(note).status_code == 200
    assert not _user(db, email).is_premium_active


def test_rtdn_voided_lifetime_revokes_premium(play, db):
    c, email, csrf = make_auth_client("play_void")
    play["products"]["life-tok-void"] = {"purchaseState": 0, "acknowledgementState": 1}
    assert _verify(c, csrf, SKU_LIFE, "life-tok-void").status_code == 200
    note = {"packageName": "club.test.app",
            "voidedPurchaseNotification": {"purchaseToken": "life-tok-void", "productType": 2}}
    assert _rtdn(note).status_code == 200
    assert not _user(db, email).is_premium


def test_rtdn_ignores_other_package_and_unknown_tokens(play):
    other = {"packageName": "com.other", "subscriptionNotification": {"purchaseToken": "x"}}
    unknown = {"packageName": "club.test.app", "subscriptionNotification": {"purchaseToken": "never-verified"}}
    assert _rtdn(other).status_code == 200
    assert _rtdn(unknown).status_code == 200


def test_rtdn_malformed_message_is_acked(play):
    r = make_client().post("/billing/play/rtdn?token=rtdn-secret", json={"message": {"data": "!!!"}})
    assert r.status_code == 200


# ── assetlinks / premium page ─────────────────────────────────────────────────

def test_assetlinks_lists_app_when_configured(monkeypatch):
    monkeypatch.setenv("PLAY_PACKAGE_NAME", "club.test.app")
    monkeypatch.setenv("ANDROID_CERT_SHA256", "AA:BB")
    r = make_client().get("/.well-known/assetlinks.json")
    assert r.status_code == 200
    assert r.json()[0]["target"]["package_name"] == "club.test.app"
    assert r.json()[0]["target"]["sha256_cert_fingerprints"] == ["AA:BB"]


def test_premium_page_renders_play_plans(play):
    c, _, _ = make_auth_client("play_page")
    r = c.get("/premium")
    assert r.status_code == 200
    assert 'id="play-plans"' in r.text
    assert f"startPlayPurchase('{SKU_SUB}'" in r.text
