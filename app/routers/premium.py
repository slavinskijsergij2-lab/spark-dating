import os
from datetime import timedelta

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import desc, func
from sqlalchemy.orm import Session, joinedload

from app.auth import get_current_user
from app.csrf import validate_csrf_header
from app.database import get_db
from app.i18n import get_lang, get_translations, is_rtl
from app.models.models import ProfileView, User
from app.templates import templates
from app.utils.time import utcnow as _utcnow

router = APIRouter()

# H2: Premium requires an activation code if PREMIUM_CODES env var is set.
# Set PREMIUM_CODES=code1,code2,code3 in Railway environment to enable code-gating.
# Without PREMIUM_CODES set, activation is open (dev / test mode).
_PREMIUM_CODES: set[str] = set(
    c.strip() for c in (os.getenv("PREMIUM_CODES") or "").split(",") if c.strip()
)


@router.get("/premium", response_class=HTMLResponse)
def premium_page(request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    lang = get_lang(request, user)
    now = _utcnow()
    boost_active = bool(user.boost_until and user.boost_until > now)
    boost_remaining = 0
    if boost_active:
        boost_remaining = int((user.boost_until - now).total_seconds() / 60)
    return templates.TemplateResponse("premium.html", {
        "request": request,
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
    db: Session = Depends(get_db),
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
    db.commit()
    return JSONResponse({"success": True})


@router.post("/premium/deactivate", dependencies=[Depends(validate_csrf_header)])
def deactivate_premium(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    user.is_premium = False
    user.premium_until = None
    db.commit()
    return JSONResponse({"success": True})


@router.post("/profile/boost", dependencies=[Depends(validate_csrf_header)])
def boost_profile(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    now = _utcnow()
    if user.boost_until and user.boost_until > now:
        remaining = int((user.boost_until - now).total_seconds() / 60)
        return JSONResponse({"error": "boost_active", "remaining_minutes": remaining}, status_code=400)
    hours = 3.0 if user.is_premium_active else 0.5
    user.boost_until = now + timedelta(hours=hours)
    db.commit()
    return JSONResponse({"success": True, "minutes": int(hours * 60)})


@router.get("/profile/who-viewed", response_class=HTMLResponse)
def who_viewed_page(request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    lang = get_lang(request, user)

    total = db.query(func.count(ProfileView.id)).filter(ProfileView.viewed_id == user.id).scalar() or 0

    viewers = []
    if user.is_premium_active:
        # C1: use .all() directly — .subquery() + db.execute() was a runtime crash
        rows = (
            db.query(ProfileView.viewer_id, func.max(ProfileView.created_at).label("last_seen"))
            .filter(ProfileView.viewed_id == user.id)
            .group_by(ProfileView.viewer_id)
            .order_by(desc("last_seen"))
            .limit(50)
            .all()
        )
        viewer_ids = [r[0] for r in rows]
        last_seen_map = {r[0]: r[1] for r in rows}
        users_map = {u.id: u for u in db.query(User).options(joinedload(User.profile)).filter(User.id.in_(viewer_ids)).all() if u.profile}
        viewers = [
            {"user": users_map[vid], "last_seen": last_seen_map[vid]}
            for vid in viewer_ids if vid in users_map
        ]

    return templates.TemplateResponse("who_viewed.html", {
        "request": request,
        "user": user,
        "t": get_translations(lang),
        "rtl": is_rtl(lang),
        "lang": lang,
        "viewers": viewers,
        "total": total,
        "is_premium": user.is_premium_active,
    })
