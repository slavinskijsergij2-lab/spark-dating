import os
import secrets
from datetime import timedelta

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func as _func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.i18n import get_lang, get_translations, is_rtl
from app.models.models import User
from app.rate_limit import rate_limit
from app.templates import templates
from app.utils.time import utcnow as _utcnow

router = APIRouter()

APP_URL = os.getenv("APP_URL", "https://spark-dating.club")
REFERRAL_BONUS_DAYS = 3


async def _generate_referral_code(db: AsyncSession) -> str:
    """Generate a unique 8-character uppercase referral code."""
    while True:
        code = secrets.token_urlsafe(6).upper()[:8]
        result = await db.execute(select(User).where(User.referral_code == code))
        if not result.scalar_one_or_none():
            return code


async def apply_referral_bonus(referrer: User, db: AsyncSession) -> None:
    """Add REFERRAL_BONUS_DAYS days of premium_until to the referrer."""
    now = _utcnow()
    base = referrer.premium_until if referrer.premium_until and referrer.premium_until > now else now
    referrer.premium_until = base + timedelta(days=REFERRAL_BONUS_DAYS)
    await db.commit()


@router.get("/referral", response_class=HTMLResponse, dependencies=[Depends(rate_limit(30, 60))])
async def referral_page(request: Request, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    if not user.referral_code:
        user.referral_code = await _generate_referral_code(db)
        await db.commit()

    result = await db.execute(
        select(_func.count(User.id)).where(User.referred_by_id == user.id)
    )
    referred_count = result.scalar() or 0
    days_earned = referred_count * REFERRAL_BONUS_DAYS

    now = _utcnow()
    bonus_active = bool(user.premium_until and user.premium_until > now)
    bonus_until = user.premium_until

    lang = get_lang(request, user)
    return templates.TemplateResponse(request, "referral.html", {
        "user": user,
        "t": get_translations(lang),
        "rtl": is_rtl(lang),
        "lang": lang,
        "referral_link": f"{APP_URL}/register?ref={user.referral_code}",
        "referred_count": referred_count,
        "days_earned": days_earned,
        "bonus_active": bonus_active,
        "bonus_until": bonus_until,
    })
