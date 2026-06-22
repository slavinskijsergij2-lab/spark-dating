"""
In-memory rate limiter (IP + path based). Suitable for a single Railway dyno.
For multi-instance deployments, swap _store for Redis.

Set TESTING=1 to disable limiting in the test suite.
"""
import asyncio
import logging
import os
import time
from collections import defaultdict
from collections.abc import Callable

from fastapi import HTTPException, Request

_store: dict[str, list[float]] = defaultdict(list)
_lock = asyncio.Lock()

if os.getenv("TESTING") and os.getenv("RAILWAY_ENVIRONMENT"):
    logging.critical("TESTING=1 is set in a Railway environment — rate limiting is DISABLED globally!")


def rate_limit(max_calls: int, window_seconds: int = 60) -> Callable:
    """Returns a FastAPI async dependency that enforces a rate limit per IP + path."""
    async def dependency(request: Request) -> None:
        if os.getenv("TESTING"):
            return  # no-op in test suite
        # Take the rightmost IP from X-Forwarded-For (the one added by Railway's trusted proxy).
        # The leftmost entry is user-controlled and trivially spoofed.
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            ip = forwarded.split(",")[-1].strip()
        else:
            real_ip = request.headers.get("X-Real-IP")
            ip = real_ip or (request.client.host if request.client else None) or "unknown"
        key = f"{request.url.path}:{ip}"
        now = time.monotonic()
        async with _lock:
            cutoff = now - window_seconds
            calls = [t for t in _store[key] if t > cutoff]
            if len(calls) >= max_calls:
                raise HTTPException(
                    status_code=429,
                    detail="Too many requests. Please try again later.",
                    headers={"Retry-After": str(window_seconds)},
                )
            calls.append(now)
            _store[key] = calls
            # Prevent unbounded memory growth: evict expired keys when store is large.
            if len(_store) > 5000:
                to_del = [k for k, v in _store.items() if not v or max(v) <= cutoff]
                for k in to_del:
                    del _store[k]
    return dependency
