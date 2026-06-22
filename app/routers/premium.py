import os
from datetime import timedelta

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.auth import get_current_user
from app.csrf import validate_csrf_header
from app.database import get_db
from app.i18n import get_lang, get_translations, is_rtl
from app.models.models import ProfileView, User
from app.templates import templates
from app.utils.time import utcnow as _utcnow

router = APIRouter()

_PREMIUM_CODES: set[str] = set(
    c.strip() for c in (os.getenv("PREMIUM_CODES") or "").split(",") if c.strip()
)


@router.get("/premium", response_class=HTMLResponse)
async def premium_page(request: Request, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    lang = get_lang(request, user)
    now = _utcnow()
    boost_active = bool(user.boost_until and user.boost_until > now)
    boost_remaining = 0
    if boost_active:
        boost_remaining = int((user.boost_until - now).total_seconds() / 60)
    return templates.TemplateResponse(request, "premium.html", {
        "user": user,
        "t": get_translations(lang),
        "rtl": is_rtl(lang),
        "lang": lang,
        "boost_active": boost_active,
        "boost_remaining": boost_remaining,
        "requires_code": bool(_PREMIUM_CODES),
    })


@router.post("/premium/activate", dependencies=[Depends(validate_csrf_header)])
async def activate_premium(
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if _PREMIUM_CODES:
        try:
            data = await request.json()
        except Exception:
            data = {}
        code = (data.get("code") or "").strip()
        if code not in _PREMIUM_CODES:
            lang = get_lang(request, user)
            t = get_translations(lang)
            return JSONResponse({"error": t.get("premium_code_invalid", "Invalid activation code")}, status_code=400)
    user.is_premium = True
    await db.commit()
    return JSONResponse({"success": True})


@router.post("/premium/deactivate", dependencies=[Depends(validate_csrf_header)])
async def deactivate_premium(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    user.is_premium = False
    user.premium_until = None
    await db.commit()
    return JSONResponse({"success": True})


@router.post("/profile/boost", dependencies=[Depends(validate_csrf_header)])
async def boost_profile(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(User).where(User.id == user.id).with_for_update()
    )
    locked = result.scalar_one_or_none()
    now = _utcnow()
    if locked.boost_until and locked.boost_until > now:
        remaining = int((locked.boost_until - now).total_seconds() / 60)
        return JSONResponse({"error": "boost_active", "remaining_minutes": remaining}, status_code=400)
    hours = 3.0 if locked.is_premium_active else 0.5
    locked.boost_until = now + timedelta(hours=hours)
    await db.commit()
    return JSONResponse({"success": True, "minutes": int(hours * 60)})


@router.get("/profile/who-viewed", response_class=HTMLResponse)
async def who_viewed_page(request: Request, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    lang = get_lang(request, user)

    result = await db.execute(
        select(func.count(ProfileView.id)).where(ProfileView.viewed_id == user.id)
    )
    total = result.scalar() or 0

    viewers = []
    if user.is_premium_active:
        result = await db.execute(
            select(ProfileView.viewer_id, func.max(ProfileView.created_at).label("last_seen"))
            .where(ProfileView.viewed_id == user.id)
            .group_by(ProfileView.viewer_id)
            .order_by(desc("last_seen"))
            .limit(50)
        )
        rows = result.all()
        viewer_ids = [r[0] for r in rows]
        last_seen_map = {r[0]: r[1] for r in rows}
        result = await db.execute(
            select(User).options(joinedload(User.profile)).where(User.id.in_(viewer_ids))
        )
        users_map = {u.id: u for u in result.scalars().unique().all() if u.profile}
        viewers = [
            {"user": users_map[vid], "last_seen": last_seen_map[vid]}
            for vid in viewer_ids if vid in users_map
        ]

    return templates.TemplateResponse(request, "who_viewed.html", {
        "user": user,
        "t": get_translations(lang),
        "rtl": is_rtl(lang),
        "lang": lang,
        "viewers": viewers,
        "total": total,
        "is_premium": user.is_premium_active,
    })
