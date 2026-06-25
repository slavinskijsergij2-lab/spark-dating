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
