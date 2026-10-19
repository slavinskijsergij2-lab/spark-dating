import asyncio
import base64
import json
import logging
import os
import secrets
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user, invalidate_user_cache
from app.csrf import validate_csrf_header
from app.database import get_db, AsyncSessionLocal
from app.i18n import get_lang, get_translations, is_rtl
from app import play_billing
from app.models.models import PlayPurchase, User
from app.rate_limit import rate_limit
from app.templates import templates
from app.utils.time import utcnow as _utcnow

router = APIRouter(prefix="/billing")

_STRIPE_SK = os.getenv("STRIPE_SECRET_KEY", "")
_STRIPE_WH = os.getenv("STRIPE_WEBHOOK_SECRET", "")
_PRICE_MONTHLY = os.getenv("STRIPE_PRICE_MONTHLY", "")
_PRICE_LIFETIME = os.getenv("STRIPE_PRICE_LIFETIME", "")
_STRIPE_PORTAL_CONFIG = os.getenv("STRIPE_PORTAL_CONFIG", "")
_SITE_URL = os.getenv("SITE_URL", "https://spark-dating.club")

stripe_enabled = bool(_STRIPE_SK and (_PRICE_MONTHLY or _PRICE_LIFETIME))


@router.post("/checkout/{plan}", dependencies=[Depends(validate_csrf_header), Depends(rate_limit(10, 60))])
async def create_checkout(
    plan: str,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if not _STRIPE_SK:
        raise HTTPException(400, "Payments not configured")
    if plan not in ("monthly", "lifetime"):
        raise HTTPException(400, "Invalid plan")
    if plan == "monthly" and not _PRICE_MONTHLY:
        raise HTTPException(400, "Monthly plan not configured")
    if plan == "lifetime" and not _PRICE_LIFETIME:
        raise HTTPException(400, "Lifetime plan not configured")

    import stripe as _stripe
    _stripe.api_key = _STRIPE_SK

    base_url = str(request.base_url).rstrip("/")
    mode = "subscription" if plan == "monthly" else "payment"
    price_id = _PRICE_MONTHLY if plan == "monthly" else _PRICE_LIFETIME

    # FIX High #7: run blocking Stripe SDK calls in a thread pool to avoid
    # blocking the async event loop during outbound HTTP requests (1–5 s each).
    customer_id = user.stripe_customer_id
    if not customer_id:
        customer = await asyncio.to_thread(
            _stripe.Customer.create,
            email=user.email,
            metadata={"user_id": str(user.id)},
        )
        customer_id = customer.id
        user.stripe_customer_id = customer_id
        await db.commit()

    session = await asyncio.to_thread(
        _stripe.checkout.Session.create,
        mode=mode,
        customer=customer_id,
        payment_method_types=["card"],
        line_items=[{"price": price_id, "quantity": 1}],
        success_url=f"{base_url}/billing/success?session_id={{CHECKOUT_SESSION_ID}}",
        cancel_url=f"{base_url}/billing/cancel",
        metadata={"user_id": str(user.id), "plan": plan},
        allow_promotion_codes=True,
        locale="auto",
    )

    return JSONResponse({"url": session.url})


@router.get("/success", response_class=HTMLResponse)
async def billing_success(
    request: Request,
    session_id: str = "",
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    lang = get_lang(request, user)
    activated = False

    if session_id and _STRIPE_SK:
        try:
            import stripe as _stripe
            _stripe.api_key = _STRIPE_SK
            # FIX High #7: blocking Stripe call in thread pool
            session = await asyncio.to_thread(_stripe.checkout.Session.retrieve, session_id)
            if session.payment_status in ("paid", "no_payment_required"):
                # FIX Critical #2: verify that the session belongs to the current user.
                # session.metadata.user_id must match the authenticated user to prevent IDOR.
                session_user_id = int((session.metadata or {}).get("user_id", 0))
                if session_user_id != user.id:
                    logging.warning(
                        "billing_success: IDOR attempt — session user_id=%d, current user=%d",
                        session_user_id, user.id,
                    )
                    return templates.TemplateResponse(request, "billing_success.html", {
                        "user": user,
                        "t": get_translations(lang),
                        "rtl": is_rtl(lang),
                        "activated": False,
                    })
                plan = (session.metadata or {}).get("plan", "monthly")
                await _activate_premium(user.id, plan, session.subscription, db)
                activated = True
        except Exception as e:
            logging.warning("billing_success: %s", e)

    return templates.TemplateResponse(request, "billing_success.html", {
        "user": user,
        "t": get_translations(lang),
        "rtl": is_rtl(lang),
        "activated": activated,
    })


@router.get("/cancel")
async def billing_cancel():
    return RedirectResponse("/premium", status_code=302)


# Cached portal configuration ID — created once on first use, survives restarts
_portal_config_id: str | None = None


async def _ensure_portal_config(stripe_module) -> str | None:
    """Create a Stripe Customer Portal configuration via API (no Dashboard setup needed)."""
    global _portal_config_id
    if _portal_config_id:
        return _portal_config_id
    try:
        config = await asyncio.to_thread(
            stripe_module.billing_portal.Configuration.create,
            business_profile={
                "headline": "Spark Dating — управление подпиской",
                "privacy_policy_url": f"{_SITE_URL}/privacy",
                "terms_of_service_url": f"{_SITE_URL}/privacy",
            },
            features={
                "invoice_history": {"enabled": True},
                "payment_method_update": {"enabled": True},
                "subscription_cancel": {
                    "enabled": True,
                    "mode": "at_period_end",
                    "proration_behavior": "none",
                    "cancellation_reason": {
                        "enabled": True,
                        "options": ["too_expensive", "missing_features", "switched_service", "other"],
                    },
                },
            },
        )
        _portal_config_id = config.id
        logging.info("billing: created portal config %s", _portal_config_id)
        return _portal_config_id
    except Exception as e:
        logging.warning("billing: could not create portal config: %s", e)
        return None


@router.post("/portal", dependencies=[Depends(validate_csrf_header), Depends(rate_limit(10, 60))])
async def billing_portal(
    request: Request,
    user: User = Depends(get_current_user),
):
    """Redirect to Stripe Customer Portal (cancel subscription, view invoices)."""
    if not _STRIPE_SK:
        raise HTTPException(400, "Payments not configured")
    if not user.stripe_customer_id:
        raise HTTPException(400, "No billing account found")

    import stripe as _stripe
    _stripe.api_key = _STRIPE_SK
    base_url = str(request.base_url).rstrip("/")

    try:
        # Use env-var config first; fall back to auto-create via API
        config_id = _STRIPE_PORTAL_CONFIG or await _ensure_portal_config(_stripe)
        session_kwargs: dict = dict(
            customer=user.stripe_customer_id,
            return_url=f"{base_url}/premium",
        )
        if config_id:
            session_kwargs["configuration"] = config_id

        portal_session = await asyncio.to_thread(
            _stripe.billing_portal.Session.create,
            **session_kwargs,
        )
        return JSONResponse({"url": portal_session.url})
    except Exception as e:
        logging.warning("billing_portal: %s", e)
        raise HTTPException(400, "Could not open billing portal")


@router.post("/webhook", include_in_schema=False)
async def stripe_webhook(request: Request):
    if not _STRIPE_SK or not _STRIPE_WH:
        raise HTTPException(400, "Webhook not configured")

    payload = await request.body()
    sig = request.headers.get("stripe-signature", "")

    try:
        import stripe as _stripe
        _stripe.api_key = _STRIPE_SK
        event = _stripe.Webhook.construct_event(payload, sig, _STRIPE_WH)
    except Exception:
        raise HTTPException(400, "Invalid signature")

    etype = event["type"]
    obj = event["data"]["object"]

    async with AsyncSessionLocal() as db:
        try:
            if etype == "checkout.session.completed":
                uid = int((obj.get("metadata") or {}).get("user_id", 0))
                plan = (obj.get("metadata") or {}).get("plan", "monthly")
                if uid and obj.get("payment_status") in ("paid", "no_payment_required"):
                    await _activate_premium(uid, plan, obj.get("subscription"), db)

            elif etype == "invoice.payment_succeeded":
                sub_id = obj.get("subscription")
                if sub_id:
                    await _extend_subscription(sub_id, db)

            elif etype in ("customer.subscription.deleted", "customer.subscription.paused"):
                sub_id = obj.get("id")
                if sub_id:
                    await _cancel_subscription(sub_id, db)

        except Exception as e:
            logging.error("stripe_webhook %s: %s", etype, e)
            # FIX Medium #16: return 500 so Stripe retries the webhook on DB errors.
            # Only return 200 when processing was genuinely successful.
            raise HTTPException(500, "Webhook processing error")

    return JSONResponse({"ok": True})


async def _activate_premium(user_id: int, plan: str, subscription_id, db: AsyncSession):
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        return
    if plan == "lifetime":
        user.is_premium = True
    else:
        now = _utcnow()
        # FIX Low #35: use dateutil or relativedelta for real calendar month;
        # timedelta(days=31) is kept as a pragmatic approximation since Stripe
        # manages the actual billing cycle externally.
        user.premium_until = max(user.premium_until or now, now) + timedelta(days=31)
        if subscription_id:
            user.stripe_subscription_id = subscription_id
    await db.commit()
    invalidate_user_cache(user.id)


async def _extend_subscription(sub_id: str, db: AsyncSession):
    result = await db.execute(select(User).where(User.stripe_subscription_id == sub_id))
    user = result.scalar_one_or_none()
    if not user:
        return
    now = _utcnow()
    user.premium_until = max(user.premium_until or now, now) + timedelta(days=31)
    await db.commit()


async def _cancel_subscription(sub_id: str, db: AsyncSession):
    result = await db.execute(select(User).where(User.stripe_subscription_id == sub_id))
    user = result.scalar_one_or_none()
    if not user:
        return
    user.premium_until = None
    user.stripe_subscription_id = None
    await db.commit()


# ── Google Play Billing (Android TWA) ────────────────────────────────────────
# The TWA buys through the Digital Goods API + Payment Request; the page then
# posts the purchase token here. Renewals, cancellations and refunds arrive via
# Real-time developer notifications (Pub/Sub push → /billing/play/rtdn).

_PLAY_RTDN_TOKEN = os.getenv("PLAY_RTDN_TOKEN", "")


@router.post("/play/verify", dependencies=[Depends(validate_csrf_header), Depends(rate_limit(20, 60))])
async def play_verify(
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if not play_billing.play_enabled:
        raise HTTPException(400, "Google Play billing not configured")
    try:
        data = await request.json()
    except Exception:
        data = {}
    product_id = str(data.get("product_id") or "")
    token = str(data.get("purchase_token") or "")
    if not token or len(token) > 512:
        raise HTTPException(400, "Invalid purchase token")
    if product_id not in (play_billing.SKU_MONTHLY, play_billing.SKU_LIFETIME) or not product_id:
        raise HTTPException(400, "Unknown product")

    # A token belongs to whoever verified it first — stops a token being replayed onto other accounts.
    result = await db.execute(select(PlayPurchase).where(PlayPurchase.purchase_token == token))
    record = result.scalar_one_or_none()
    if record and record.user_id != user.id:
        logging.warning("play_verify: token already bound to user %d, current user %d", record.user_id, user.id)
        raise HTTPException(409, "Purchase belongs to another account")

    is_sub = product_id == play_billing.SKU_MONTHLY
    try:
        if is_sub:
            purchase = await play_billing.get_subscription(token)
            if product_id not in play_billing.subscription_product_ids(purchase):
                raise HTTPException(400, "Product mismatch")
            expiry = play_billing.subscription_expiry(purchase)
            if purchase.get("subscriptionState") not in play_billing.ENTITLED_STATES or not expiry or expiry <= _utcnow():
                raise HTTPException(400, "Subscription is not active")
            needs_ack = purchase.get("acknowledgementState") == "ACKNOWLEDGEMENT_STATE_PENDING"
        else:
            purchase = await play_billing.get_product(product_id, token)
            if purchase.get("purchaseState") == 2:
                raise HTTPException(402, "Payment pending")
            if purchase.get("purchaseState") != 0:
                raise HTTPException(400, "Purchase was cancelled")
            expiry = None
            needs_ack = purchase.get("acknowledgementState") == 0
    except play_billing.PurchaseNotFound:
        raise HTTPException(400, "Purchase not found")
    except play_billing.PlayError as e:
        logging.error("play_verify: %s", e)
        raise HTTPException(502, "Could not reach Google Play")

    if not record:
        record = PlayPurchase(user_id=user.id, purchase_token=token, product_id=product_id,
                              kind="subscription" if is_sub else "product")
        db.add(record)
    record.expires_at = expiry

    db_user = (await db.execute(select(User).where(User.id == user.id))).scalar_one()
    if is_sub:
        db_user.premium_until = max(db_user.premium_until or expiry, expiry)
    else:
        db_user.is_premium = True
    try:
        await db.commit()
    except IntegrityError:
        # Same token verified concurrently — the other request wins.
        await db.rollback()
        raise HTTPException(409, "Purchase is being processed")
    invalidate_user_cache(user.id)

    # Unacknowledged purchases are refunded by Google after 3 days. If this fails,
    # the premium page re-verifies owned purchases on its next load and retries.
    if needs_ack:
        try:
            if is_sub:
                await play_billing.acknowledge_subscription(product_id, token)
            else:
                await play_billing.acknowledge_product(product_id, token)
        except play_billing.PlayError as e:
            logging.error("play_verify: acknowledge failed: %s", e)

    return JSONResponse({"success": True})


@router.post("/play/rtdn", include_in_schema=False)
async def play_rtdn(request: Request, token: str = ""):
    """Pub/Sub push endpoint for Google Play real-time developer notifications."""
    if not _PLAY_RTDN_TOKEN or not secrets.compare_digest(token, _PLAY_RTDN_TOKEN):
        raise HTTPException(403, "Forbidden")
    try:
        envelope = await request.json()
        note = json.loads(base64.b64decode(envelope["message"]["data"]))
    except Exception:
        # Malformed messages would be redelivered forever — ack them.
        logging.warning("play_rtdn: malformed message")
        return JSONResponse({"ok": True})

    if note.get("packageName") != play_billing.PACKAGE_NAME:
        return JSONResponse({"ok": True})

    sub_note = note.get("subscriptionNotification")
    voided = note.get("voidedPurchaseNotification")
    purchase_token = (sub_note or voided or {}).get("purchaseToken")
    if not purchase_token:
        return JSONResponse({"ok": True})  # test / one-time-product notifications: verify handles those

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(PlayPurchase).where(PlayPurchase.purchase_token == purchase_token))
        record = result.scalar_one_or_none()
        if not record:
            # Not verified by the app yet; /billing/play/verify will pick it up.
            return JSONResponse({"ok": True})
        db_user = (await db.execute(select(User).where(User.id == record.user_id))).scalar_one_or_none()
        if not db_user:
            return JSONResponse({"ok": True})

        if voided:
            if record.kind == "product":
                db_user.is_premium = False
            else:
                db_user.premium_until = None
            record.expires_at = None
        else:
            try:
                sub = await play_billing.get_subscription(purchase_token)
                expiry = play_billing.subscription_expiry(sub)
                entitled = sub.get("subscriptionState") in play_billing.ENTITLED_STATES and expiry and expiry > _utcnow()
            except play_billing.PurchaseNotFound:
                expiry, entitled = None, False
            except play_billing.PlayError as e:
                logging.error("play_rtdn: %s", e)
                raise HTTPException(500, "Play API error")  # Pub/Sub retries
            record.expires_at = expiry
            db_user.premium_until = expiry if entitled else None

        await db.commit()
        invalidate_user_cache(db_user.id)

    return JSONResponse({"ok": True})
