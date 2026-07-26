import asyncio
import logging
import os
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user, invalidate_user_cache
from app.csrf import validate_csrf_header
from app.database import get_db, AsyncSessionLocal
from app.i18n import get_lang, get_translations, is_rtl
from app.models.models import User
from app.rate_limit import rate_limit
from app.templates import templates
from app.utils.time import utcnow as _utcnow

router = APIRouter(prefix="/billing")

_STRIPE_SK = os.getenv("STRIPE_SECRET_KEY", "")
_STRIPE_WH = os.getenv("STRIPE_WEBHOOK_SECRET", "")
_PRICE_MONTHLY = os.getenv("STRIPE_PRICE_MONTHLY", "")
_PRICE_LIFETIME = os.getenv("STRIPE_PRICE_LIFETIME", "")

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
