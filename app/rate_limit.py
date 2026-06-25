"""
Rate limiter (IP + path based).

Backend selection:
  - REDIS_URL set → Redis sorted-set rate limiter (survives restarts, works
    across multiple Railway replicas).
  - REDIS_URL not set → in-memory fallback (single-dyno only, resets on restart).

Set TESTING=1 to disable limiting in the test suite.
"""
import asyncio
import logging
import os
import time
from collections import defaultdict
from collections.abc import Callable

from fastapi import HTTPException, Request

# ── in-memory fallback ────────────────────────────────────────────────────────
_store: dict[str, list[float]] = defaultdict(list)
_lock = asyncio.Lock()

# ── Redis backend (optional) ──────────────────────────────────────────────────
_REDIS_URL = os.getenv("REDIS_URL")
_redis_client = None
_redis_ok = True  # flipped to False on first connection failure, retried after _RETRY_INTERVAL
_redis_fail_at: float = 0.0
_RETRY_INTERVAL = 60.0  # seconds before we try Redis again after a failure


async def _get_redis():
    global _redis_client, _redis_ok, _redis_fail_at
    if not _REDIS_URL:
        return None
    if not _redis_ok:
        if time.monotonic() - _redis_fail_at < _RETRY_INTERVAL:
            return None
        _redis_ok = True  # retry
    if _redis_client is None:
        try:
            import redis.asyncio as aioredis
            _redis_client = aioredis.from_url(
                _REDIS_URL,
                encoding="utf-8",
                decode_responses=True,
                socket_connect_timeout=2,
                socket_timeout=2,
            )
            await _redis_client.ping()
            logging.info("rate_limit: Redis connected (%s)", _REDIS_URL.split("@")[-1])
        except Exception as exc:
            logging.warning("rate_limit: Redis unavailable (%s) — using in-memory fallback", exc)
            _redis_client = None
            _redis_ok = False
            _redis_fail_at = time.monotonic()
    return _redis_client


async def _redis_check(redis, key: str, max_calls: int, window_seconds: int) -> bool:
    """Returns True if the request should be rate-limited (limit exceeded)."""
    now = time.time()
    member = f"{now:.6f}:{os.urandom(3).hex()}"
    try:
        async with redis.pipeline(transaction=True) as pipe:
            pipe.zremrangebyscore(key, 0, now - window_seconds)
            pipe.zadd(key, {member: now})
            pipe.zcard(key)
            pipe.expire(key, window_seconds + 1)
            results = await pipe.execute()
        count = results[2]
        return count > max_calls
    except Exception as exc:
        global _redis_ok, _redis_fail_at, _redis_client
        logging.warning("rate_limit: Redis error during check (%s) — falling back", exc)
        _redis_client = None
        _redis_ok = False
        _redis_fail_at = time.monotonic()
        return False  # fail open rather than blocking all traffic


if os.getenv("TESTING") and os.getenv("RAILWAY_ENVIRONMENT"):
    logging.critical("TESTING=1 is set in a Railway environment — rate limiting is DISABLED globally!")


def rate_limit(max_calls: int, window_seconds: int = 60) -> Callable:
    """Returns a FastAPI async dependency that enforces a rate limit per IP + path."""
    from fastapi import Response

    async def dependency(request: Request, response: Response) -> None:
        if os.getenv("TESTING"):
            return

        # Railway's trusted proxy appends the real client IP as the rightmost entry.
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            ip = forwarded.split(",")[-1].strip()
        else:
            real_ip = request.headers.get("X-Real-IP")
            ip = real_ip or (request.client.host if request.client else None) or "unknown"

        key = f"rl:{request.url.path}:{ip}"

        redis = await _get_redis()
        if redis is not None:
            limited = await _redis_check(redis, key, max_calls, window_seconds)
            if limited:
                raise HTTPException(
                    status_code=429,
                    detail="Too many requests. Please try again later.",
                    headers={"Retry-After": str(window_seconds)},
                )
            return

        # ── in-memory fallback ────────────────────────────────────────────────
        now = time.monotonic()
        async with _lock:
            cutoff = now - window_seconds
            calls = [t for t in _store[key] if t > cutoff]
            remaining = max(0, max_calls - len(calls) - 1)
            if len(calls) >= max_calls:
                raise HTTPException(
                    status_code=429,
                    detail="Too many requests. Please try again later.",
                    headers={
                        "Retry-After": str(window_seconds),
                        "X-RateLimit-Limit": str(max_calls),
                        "X-RateLimit-Remaining": "0",
                        "X-RateLimit-Reset": str(int(now + window_seconds)),
                    },
                )
            calls.append(now)
            _store[key] = calls
            if len(_store) > 5000:
                evict_before = now - 3600
                to_del = [k for k, v in _store.items() if not v or max(v) <= evict_before]
                for k in to_del:
                    del _store[k]

        response.headers["X-RateLimit-Limit"] = str(max_calls)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        response.headers["X-RateLimit-Reset"] = str(int(now + window_seconds))

    return dependency
