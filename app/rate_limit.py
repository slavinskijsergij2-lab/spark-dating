"""
In-memory rate limiter (IP + path based). Suitable for a single Railway dyno.
For multi-instance deployments, swap _store for Redis.

Set TESTING=1 to disable limiting in the test suite.
"""
import os
import time
from collections import defaultdict
from collections.abc import Callable
from threading import Lock

from fastapi import HTTPException, Request

_store: dict[str, list[float]] = defaultdict(list)
_lock = Lock()


def rate_limit(max_calls: int, window_seconds: int = 60) -> Callable[[Request], None]:
    """Returns a FastAPI dependency that enforces a rate limit per IP + path."""
    def dependency(request: Request) -> None:
        if os.getenv("TESTING"):
            return  # no-op in test suite
        # Behind Railway/Nginx proxy the real IP is in X-Forwarded-For
        forwarded = request.headers.get("X-Forwarded-For") or request.headers.get("X-Real-IP")
        if forwarded:
            ip = forwarded.split(",")[0].strip()
        else:
            ip = (request.client.host if request.client else None) or "unknown"
        key = f"{request.url.path}:{ip}"
        now = time.monotonic()
        with _lock:
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
            # Prevent unbounded memory growth: evict all empty/expired keys when store is large.
            if len(_store) > 5000:
                to_del = [k for k, v in _store.items() if not v or max(v) <= now - window_seconds]
                for k in to_del:
                    del _store[k]
    return dependency
