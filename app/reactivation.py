"""Reactivation emails: users inactive for 3–4 days who have matches or new likes.

Called once a day by the background loop in main.py. The loop restarts with every
deploy, so each user is claimed (Redis, with an in-process fallback) before sending:
several deploys on one day must not send the same person several emails.
"""
import asyncio
import logging
import time
from datetime import timedelta

from sqlalchemy import func, select

from app.database import AsyncSessionLocal
from app.email_utils import send_reactivation_email
from app.models.models import Like, Match, Profile, User
from app.utils.time import utcnow

# The inactivity window is one day wide; two days covers it with margin and still lets
# a user who came back and went quiet again get a new email later.
_CLAIM_TTL = 2 * 86400
_local_claims: dict[int, float] = {}


async def _claim(user_id: int) -> bool:
    """True if nobody has emailed this user within _CLAIM_TTL."""
    from app.rate_limit import _get_redis
    try:
        redis = await _get_redis()
        if redis is not None:
            return bool(await redis.set(f"reactivation:sent:{user_id}", "1", nx=True, ex=_CLAIM_TTL))
    except Exception as exc:
        logging.warning("reactivation: Redis claim failed (%s) — using in-process fallback", exc)
    now = time.time()
    if now - _local_claims.get(user_id, 0.0) < _CLAIM_TTL:
        return False
    _local_claims[user_id] = now
    return True


async def _release(user_id: int) -> None:
    from app.rate_limit import _get_redis
    _local_claims.pop(user_id, None)
    try:
        redis = await _get_redis()
        if redis is not None:
            await redis.delete(f"reactivation:sent:{user_id}")
    except Exception:
        pass


async def send_reactivation_emails() -> tuple[int, int]:
    """Send the emails; returns (sent, candidates)."""
    now = utcnow()
    cutoff_min = now - timedelta(days=4)
    cutoff_max = now - timedelta(days=3)

    async with AsyncSessionLocal() as session:
        rows = (await session.execute(
            select(User.id, User.email, User.language, Profile.name)
            .join(Profile, Profile.user_id == User.id)
            .where(
                User.email_verified.is_(True),
                User.is_active.is_(True),
                User.notif_matches.is_(True),
                User.last_seen >= cutoff_min,
                User.last_seen < cutoff_max,
            )
        )).all()
        if not rows:
            return 0, 0
        user_ids = [r.id for r in rows]

        match_counts: dict[int, int] = {}
        for col in (Match.user1_id, Match.user2_id):
            result = await session.execute(
                select(col, func.count()).where(col.in_(user_ids)).group_by(col)
            )
            for uid, cnt in result:
                match_counts[uid] = match_counts.get(uid, 0) + cnt

        # Likes received since each user was last online. Comparing against the users
        # row keeps the cutoff a real timestamp column: the previous hand-built
        # "CASE WHEN liked_id = N THEN :param END" made PostgreSQL type the bind
        # parameter as text and reject the query ("timestamp >= text").
        result = await session.execute(
            select(Like.liked_id, func.count())
            .join(User, User.id == Like.liked_id)
            .where(
                Like.liked_id.in_(user_ids),
                Like.is_like.is_(True),
                Like.created_at >= User.last_seen,
            )
            .group_by(Like.liked_id)
        )
        like_counts: dict[int, int] = {uid: cnt for uid, cnt in result}

    sent = 0
    for row in rows:
        matches = match_counts.get(row.id, 0)
        likes = like_counts.get(row.id, 0)
        if matches == 0 and likes == 0:
            continue
        if not await _claim(row.id):
            continue
        ok = await asyncio.to_thread(
            send_reactivation_email, row.email, row.name, matches, likes, row.language or "ru",
        )
        if ok:
            sent += 1
        else:
            await _release(row.id)
    return sent, len(rows)
