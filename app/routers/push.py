"""Web Push subscription management endpoints."""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select, delete as _delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.csrf import validate_csrf_header
from app.database import get_db
from app.models.models import PushSubscription, User
from app.push import vapid_public_key, push_enabled
from app.rate_limit import rate_limit

router = APIRouter()


@router.get("/api/vapid-public-key")
async def get_vapid_key():
    return JSONResponse({"key": vapid_public_key(), "enabled": push_enabled()})


@router.post("/push/subscribe", dependencies=[Depends(validate_csrf_header), Depends(rate_limit(10, 60))])
async def subscribe(
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    body = await request.json()
    endpoint = body.get("endpoint", "").strip()
    p256dh = body.get("keys", {}).get("p256dh", "").strip()
    auth = body.get("keys", {}).get("auth", "").strip()

    if not endpoint or not p256dh or not auth:
        return JSONResponse({"error": "Invalid subscription"}, status_code=400)

    result = await db.execute(
        select(PushSubscription).where(PushSubscription.endpoint == endpoint)
    )
    existing = result.scalar_one_or_none()
    if existing:
        existing.user_id = user.id
        existing.p256dh = p256dh
        existing.auth = auth
    else:
        db.add(PushSubscription(user_id=user.id, endpoint=endpoint, p256dh=p256dh, auth=auth))

    await db.commit()
    return JSONResponse({"ok": True})


@router.post("/push/unsubscribe", dependencies=[Depends(validate_csrf_header), Depends(rate_limit(10, 60))])
async def unsubscribe(
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    body = await request.json()
    endpoint = body.get("endpoint", "").strip()
    if endpoint:
        await db.execute(
            _delete(PushSubscription).where(
                PushSubscription.endpoint == endpoint,
                PushSubscription.user_id == user.id,
            )
        )
        await db.commit()
    return JSONResponse({"ok": True})
