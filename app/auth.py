import logging
import os
from datetime import timedelta
from typing import Optional

from fastapi import Depends, HTTPException, Request, status
import bcrypt as _bcrypt
import jwt as _jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models.models import User

_SECRET_KEY = os.getenv("SECRET_KEY", "")
if not _SECRET_KEY:
    _is_production = bool(os.getenv("RAILWAY_ENVIRONMENT"))
    if _is_production:
        raise RuntimeError(
            "SECRET_KEY env var is required in production. "
            "Set it in Railway environment variables."
        )
    _SECRET_KEY = "change-this-in-dev-only-do-not-use-in-prod"
    logging.warning(
        "SECRET_KEY env var is not set! Using insecure default key — development only. "
        "Set SECRET_KEY in Railway environment variables before going live."
    )

SECRET_KEY = _SECRET_KEY
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 7  # 7 days

DUMMY_HASH = _bcrypt.hashpw(b"timing-safe-dummy-spark", _bcrypt.gensalt()).decode()

from app.utils.time import utcnow as _utcnow


def hash_password(password: str) -> str:
    return _bcrypt.hashpw(password.encode(), _bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return _bcrypt.checkpw(plain.encode(), hashed.encode())
    except Exception:
        return False


def create_access_token(user_id: int) -> str:
    expire = _utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    return _jwt.encode({"sub": str(user_id), "exp": expire}, SECRET_KEY, algorithm=ALGORITHM)


async def get_current_user(request: Request, db: AsyncSession = Depends(get_db)) -> User:
    token = request.cookies.get("access_token")
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    try:
        payload = _jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = int(payload.get("sub"))
    except (_jwt.InvalidTokenError, TypeError, ValueError):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    result = await db.execute(
        select(User)
        .options(selectinload(User.profile))
        .where(User.id == user_id, User.is_active == True)
    )
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")

    now = _utcnow()
    if not user.last_seen or (now - user.last_seen).total_seconds() > 60:
        user.last_seen = now
        await db.commit()

    return user


async def get_optional_user(request: Request, db: AsyncSession = Depends(get_db)) -> Optional[User]:
    try:
        return await get_current_user(request, db)
    except HTTPException:
        return None
