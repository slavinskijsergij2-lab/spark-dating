"""
In-memory rate limiter (IP + path based). Suitable for a single Railway dyno.
For multi-instance deployments, swap _store for Redis.
"""
import time
from collections import defaultdict
from threading import Lock

from fastapi import HTTPException, Request

_store: dict[str, list[float]] = defaultdict(list)
_lock = Lock()


def rate_limit(max_calls: int, window_seconds: int = 60):
    """Returns a FastAPI dependency that enforces a rate limit per IP + path."""
    def dependency(request: Request):
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
    return dependency
