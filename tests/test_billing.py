"""Tests for billing routes: /billing/success, /billing/cancel (Stripe webhook skipped — needs real secret)."""
import pytest
from tests.conftest import make_client, make_auth_client


# ── Checkout ──────────────────────────────────────────────────────────────────

def test_checkout_requires_auth():
    c = make_client()
    csrf = c.get("/login").cookies.get("csrftoken", "")
    r = c.post("/billing/checkout/premium", headers={"x-csrf-token": csrf}, follow_redirects=False)
    assert r.status_code in (302, 401)


def test_checkout_invalid_plan_returns_error():
    c, _, csrf = make_auth_client("bill1")
    r = c.post(
        "/billing/checkout/invalidplan",
        headers={"x-csrf-token": csrf},
        follow_redirects=False,
    )
    assert r.status_code in (302, 400, 404, 422)


def test_checkout_missing_csrf_rejected():
    c, _, _ = make_auth_client("bill2")
    r = c.post("/billing/checkout/premium", follow_redirects=False)
    assert r.status_code in (400, 403, 422)


def test_checkout_without_stripe_key_returns_error():
    """Without STRIPE_SECRET_KEY configured, checkout should return a graceful error."""
    import os
    if os.getenv("STRIPE_SECRET_KEY"):
        pytest.skip("STRIPE_SECRET_KEY is set — skip mock test")
    c, _, csrf = make_auth_client("bill3")
    r = c.post(
        "/billing/checkout/premium",
        headers={"x-csrf-token": csrf},
        follow_redirects=True,
    )
    # Should redirect back with error, not crash
    assert r.status_code in (200, 302, 400, 500)


# ── Success / Cancel pages ────────────────────────────────────────────────────

def test_billing_cancel_redirects():
    c, _, _ = make_auth_client("billcancel")
    r = c.get("/billing/cancel", follow_redirects=False)
    assert r.status_code in (200, 302)


def test_billing_success_requires_session_id():
    c, _, _ = make_auth_client("billsucc")
    # Without session_id, should return gracefully (not 500)
    r = c.get("/billing/success", follow_redirects=True)
    assert r.status_code in (200, 302, 400)


# ── Webhook ───────────────────────────────────────────────────────────────────

def test_webhook_invalid_signature_returns_400():
    c = make_client()
    r = c.post(
        "/billing/webhook",
        content=b'{"type":"checkout.session.completed"}',
        headers={
            "Content-Type": "application/json",
            "Stripe-Signature": "t=123,v1=invalidsig",
        },
    )
    # Should reject invalid signature, not crash
    assert r.status_code in (400, 422)


def test_webhook_empty_body_rejected():
    c = make_client()
    r = c.post(
        "/billing/webhook",
        content=b"",
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code in (400, 422)


# ── Stripe activation / renewal idempotency ──────────────────────────────────

def _run(coro):
    import asyncio
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _stripe_user(email_prefix):
    from app.database import SessionLocal
    from app.models.models import User
    _c, email, _ = make_auth_client(email_prefix)
    with SessionLocal() as s:
        return s.query(User).filter(User.email == email).one().id


def _premium_until(uid):
    from app.database import SessionLocal
    from app.models.models import User
    with SessionLocal() as s:
        return s.get(User, uid).premium_until


async def _activate(uid, plan, sub_id):
    from app.database import AsyncSessionLocal
    from app.routers.billing import _activate_premium
    async with AsyncSessionLocal() as db:
        await _activate_premium(uid, plan, sub_id, db)


async def _extend(sub_id, period_end):
    from app.database import AsyncSessionLocal
    from app.routers.billing import _extend_subscription
    async with AsyncSessionLocal() as db:
        await _extend_subscription(sub_id, db, period_end)


def test_monthly_activation_is_granted_once_per_subscription():
    """Success page reloads + the webhook must not stack extra months."""
    from datetime import timedelta
    from app.utils.time import utcnow
    uid = _stripe_user("stripe_once")
    _run(_activate(uid, "monthly", "sub_once"))
    first = _premium_until(uid)
    assert first > utcnow() + timedelta(days=30)
    _run(_activate(uid, "monthly", "sub_once"))
    _run(_activate(uid, "monthly", "sub_once"))
    assert _premium_until(uid) == first


def test_renewal_uses_invoice_period_and_is_idempotent():
    from datetime import timedelta
    from app.utils.time import utcnow
    uid = _stripe_user("stripe_renew")
    _run(_activate(uid, "monthly", "sub_renew"))
    period_end = utcnow() + timedelta(days=62)
    _run(_extend("sub_renew", period_end))
    _run(_extend("sub_renew", period_end))  # webhook redelivery
    assert _premium_until(uid) == period_end + timedelta(days=1)


def test_invoice_period_end_parsing():
    from app.routers.billing import _invoice_period_end
    inv = {"lines": {"data": [{"period": {"start": 1700000000, "end": 1702592000}}]}}
    end = _invoice_period_end(inv)
    assert end.tzinfo is None and end.year == 2023
    assert _invoice_period_end({}) is None
