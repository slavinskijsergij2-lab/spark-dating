"""
Web Push notification utility.

Requires VAPID_PUBLIC_KEY and VAPID_PRIVATE_KEY env vars (set on Railway).
VAPID_PRIVATE_KEY is stored as base64-encoded PEM to survive env var newline stripping.
If the keys are not set, all push calls are silently no-ops.
"""
import asyncio
import base64
import logging
import os
from typing import Optional

_VAPID_PUBLIC_KEY = os.getenv("VAPID_PUBLIC_KEY", "")
_VAPID_PRIVATE_KEY_B64 = os.getenv("VAPID_PRIVATE_KEY", "")
_VAPID_EMAIL = os.getenv("VAPID_EMAIL", "mailto:admin@spark-dating.club")


def vapid_public_key() -> str:
    return _VAPID_PUBLIC_KEY


def push_enabled() -> bool:
    return bool(_VAPID_PUBLIC_KEY and _VAPID_PRIVATE_KEY_B64)


def _private_pem() -> str:
    """Decode base64-encoded PEM private key from env var."""
    raw = base64.urlsafe_b64decode(_VAPID_PRIVATE_KEY_B64 + "==")
    return raw.decode()


def _build_subscription(endpoint: str, p256dh: str, auth: str) -> dict:
    return {"endpoint": endpoint, "keys": {"p256dh": p256dh, "auth": auth}}


def _send_sync(endpoint: str, p256dh: str, auth: str, title: str, body: str, url: str) -> Optional[bool]:
    """Blocking send — runs in a thread via asyncio.to_thread."""
    import json
    from pywebpush import webpush, WebPushException

    data = json.dumps({"title": title, "body": body, "url": url})
    try:
        webpush(
            subscription_info=_build_subscription(endpoint, p256dh, auth),
            data=data,
            vapid_private_key=_private_pem(),
            vapid_claims={"sub": _VAPID_EMAIL},
        )
        return True
    except WebPushException as exc:
        status = getattr(exc.response, "status_code", None) if exc.response else None
        if status in (404, 410):
            return None  # expired — caller should delete from DB
        logging.warning("push: WebPushException status=%s: %s", status, exc)
        return False
    except Exception as exc:
        logging.warning("push: error: %s", exc)
        return False


async def send_push_to_user(
    user_id: int,
    title: str,
    body: str,
    url: str = "/matches",
) -> None:
    """Send Web Push to all subscriptions of user_id. Deletes expired subs."""
    if not push_enabled():
        return

    from sqlalchemy import select, delete as _delete
    from sqlalchemy.ext.asyncio import AsyncSession
    from app.database import AsyncSessionLocal
    from app.models.models import PushSubscription

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(PushSubscription).where(PushSubscription.user_id == user_id)
        )
        subs = result.scalars().all()
        if not subs:
            return

        expired_ids = []
        for sub in subs:
            outcome = await asyncio.to_thread(
                _send_sync, sub.endpoint, sub.p256dh, sub.auth, title, body, url
            )
            if outcome is None:
                expired_ids.append(sub.id)

        if expired_ids:
            await db.execute(
                _delete(PushSubscription).where(PushSubscription.id.in_(expired_ids))
            )
            await db.commit()
