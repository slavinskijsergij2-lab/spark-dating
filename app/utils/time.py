"""Shared UTC time helper — avoids duplicating _utcnow() across every module."""
from datetime import datetime, timezone


def utcnow() -> datetime:
    """Return current UTC time as a naive datetime (matches SQLAlchemy column convention)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)
