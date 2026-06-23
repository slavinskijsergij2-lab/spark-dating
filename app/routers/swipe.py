from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import and_, case, delete, func, not_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth import get_current_user
from app.csrf import validate_csrf_header
from app.rate_limit import rate_limit
from app.utils.time import utcnow as _utcnow
from app.database import get_db
from app.email_utils import is_smtp_configured, send_match_email
from app.i18n import get_lang, get_translations, is_rtl
from app.models.models import Block, Like, Match, Profile, ProfilePhoto, User, GenderEnum
from app.templates import templates


router = APIRouter()

VALID_INTENTIONS = {"serious", "casual", "today", "browsing"}

_ONLINE_LABELS = {
    "ru": ("Онлайн", "{n} мин назад", "{n} ч назад"),
    "uk": ("Онлайн", "{n} хв тому", "{n} год тому"),
    "en": ("Online", "{n}m ago", "{n}h ago"),
    "de": ("Online", "vor {n}m", "vor {n}h"),
    "tr": ("Çevrimiçi", "{n}d önce", "{n}s önce"),
    "ar": ("متصل", "منذ {n}د", "منذ {n}س"),
}


def _online_status_for_json(last_seen, lang: str) -> dict:
    if not last_seen:
        return {"is_online": False, "label": ""}
    diff = (_utcnow() - last_seen).total_seconds()
    ol, ml, hl = _ONLINE_LABELS.get(lang, _ONLINE_LABELS["en"])
    if diff < 300:
        return {"is_online": True, "label": ol}
    if diff < 3600:
        return {"is_online": False, "label": ml.replace("{n}", str(int(diff / 60)))}
    if diff < 86400:
        return {"is_online": False, "label": hl.replace("{n}", str(int(diff / 3600)))}
    return {"is_online": False, "label": ""}


async def _candidate_to_json(candidate: User, db: AsyncSession, lang: str) -> dict:
    profile = candidate.profile
    result = await db.execute(
        select(ProfilePhoto)
        .where(ProfilePhoto.profile_id == profile.id)
        .order_by(ProfilePhoto.position)
    )
    extra = result.scalars().all()
    photos = []
    if profile.photo:
        photos.append(profile.photo)
    photos.extend(p.url for p in extra)
    raw_interests = profile.interests or ""
    interests_list = [t.strip() for t in raw_interests.split(",") if t.strip()][:4]
    os = _online_status_for_json(candidate.last_seen, lang)
    return {
        "id": candidate.id,
        "name": profile.name,
        "age": profile.age,
        "bio": profile.bio or "",
        "city": profile.city or "",
        "photos": photos,
        "is_verified": bool(candidate.is_verified),
        "is_online": os["is_online"],
        "online_label": os["label"],
        "intention": profile.intention or "",
        "interests_list": interests_list,
        "politeness_score": float(candidate.politeness_score or 0),
        "politeness_votes": int(candidate.politeness_votes or 0),
    }


async def _super_likes_today(user_id: int, db: AsyncSession) -> int:
    today_start = _utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    result = await db.execute(
        select(func.count(Like.id)).where(
            Like.liker_id == user_id,
            Like.is_super == True,
            Like.created_at >= today_start,
        )
    )
    return result.scalar() or 0


DISLIKE_RESHOW_DAYS = 7


async def find_next_candidate(
    user: User, db: AsyncSession,
    intention: str = None, age_min: int = 18, age_max: int = 100,
    city: str = None, online_only: bool = False,
):
    from datetime import timedelta

    result = await db.execute(select(Profile).where(Profile.user_id == user.id))
    profile = result.scalar_one_or_none()
    if not profile:
        return None

    now = _utcnow()

    liked_ids = select(Like.liked_id).where(
        Like.liker_id == user.id, Like.is_like == True
    ).scalar_subquery()

    dislike_cutoff = now - timedelta(days=DISLIKE_RESHOW_DAYS)
    recent_dislike_ids = select(Like.liked_id).where(
        Like.liker_id == user.id,
        Like.is_like == False,
        Like.created_at >= dislike_cutoff,
    ).scalar_subquery()

    blocked_ids = select(Block.blocked_id).where(Block.blocker_id == user.id).scalar_subquery()
    blocker_ids = select(Block.blocker_id).where(Block.blocked_id == user.id).scalar_subquery()

    q = (
        select(User)
        .join(Profile, Profile.user_id == User.id)
        .options(selectinload(User.profile))
        .where(User.id != user.id)
        .where(User.is_active == True)
        .where(not_(User.id.in_(liked_ids)))
        .where(not_(User.id.in_(recent_dislike_ids)))
        .where(not_(User.id.in_(blocked_ids)))
        .where(not_(User.id.in_(blocker_ids)))
        .where(Profile.age >= age_min)
        .where(Profile.age <= age_max)
    )

    if profile.looking_for:
        q = q.where(Profile.gender == profile.looking_for)

    if intention and intention in VALID_INTENTIONS:
        q = q.where(Profile.intention == intention)

    if city and city.strip():
        q = q.where(Profile.city.ilike(f"%{city.strip()}%"))

    if online_only:
        from datetime import timedelta
        online_threshold = now - timedelta(minutes=5)
        q = q.where(User.last_seen >= online_threshold)

    is_boosted = case((User.boost_until > now, 1), else_=0)
    q = q.order_by(is_boosted.desc(), User.politeness_score.desc(), User.id).limit(1)

    result = await db.execute(q)
    return result.scalar_one_or_none()


@router.get("/swipe", response_class=HTMLResponse)
async def swipe_page(
    request: Request,
    intention: str = Query(None),
    age_min: int = Query(18, ge=18, le=99),
    age_max: int = Query(100, ge=19, le=100),
    city: str = Query(None),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if not user.profile:
        return RedirectResponse("/profile/edit", status_code=302)

    if intention and intention not in VALID_INTENTIONS:
        intention = None
    if age_min > age_max:
        age_min, age_max = 18, 100

    city_filter = city.strip() if city and city.strip() else None
    online_only = request.query_params.get("online_only") == "1"
    candidate = await find_next_candidate(
        user, db, intention=intention, age_min=age_min, age_max=age_max,
        city=city_filter, online_only=online_only,
    )
    lang = get_lang(request, user)
    super_likes_left = max(0, (999 if user.is_premium_active else 5) - await _super_likes_today(user.id, db))

    result = await db.execute(
        select(Like).where(Like.liker_id == user.id).order_by(Like.id.desc()).limit(1)
    )
    last_like = result.scalar_one_or_none()

    extra_photos = []
    if candidate and candidate.profile:
        result = await db.execute(
            select(ProfilePhoto)
            .where(ProfilePhoto.profile_id == candidate.profile.id)
            .order_by(ProfilePhoto.position)
        )
        extra_photos = result.scalars().all()
    init_interests: list = []
    if candidate and candidate.profile and candidate.profile.interests:
        init_interests = [
            t.strip() for t in candidate.profile.interests.split(",") if t.strip()
        ][:4]

    return templates.TemplateResponse(request, "swipe.html", {
        "user": user,
        "candidate": candidate,
        "profile": candidate.profile if candidate else None,
        "extra_photos": extra_photos,
        "init_interests": init_interests,
        "t": get_translations(lang),
        "rtl": is_rtl(lang),
        "lang": lang,
        "current_intention": intention or "",
        "current_city": city_filter or "",
        "online_only": online_only,
        "age_min": age_min,
        "age_max": age_max,
        "filters_active": age_min != 18 or age_max != 100 or bool(city_filter) or online_only,
        "super_likes_left": super_likes_left,
        "can_undo": last_like is not None and user.is_premium_active,
    })


@router.post("/swipe", dependencies=[Depends(validate_csrf_header)])
async def swipe_noop(user=Depends(get_current_user)):
    return JSONResponse({"matched": False})


@router.post("/swipe/undo", dependencies=[Depends(validate_csrf_header), Depends(rate_limit(30, 60))])
async def undo_swipe(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    if not user.is_premium_active:
        return JSONResponse({"error": "Premium only"}, status_code=403)
    result = await db.execute(
        select(Like).where(Like.liker_id == user.id).order_by(Like.id.desc()).limit(1)
    )
    last_like = result.scalar_one_or_none()
    if not last_like:
        return JSONResponse({"error": "Nothing to undo"}, status_code=400)
    liked_id = last_like.liked_id
    await db.delete(last_like)
    # If the like created a match, delete it too so the undo is complete
    u1, u2 = min(user.id, liked_id), max(user.id, liked_id)
    await db.execute(delete(Match).where(Match.user1_id == u1, Match.user2_id == u2))
    await db.commit()
    return JSONResponse({"success": True})


@router.post("/swipe/{target_id}", dependencies=[Depends(validate_csrf_header), Depends(rate_limit(120, 60))])
async def do_swipe(
    target_id: int,
    action: str,
    request: Request,
    background_tasks: BackgroundTasks,
    is_super: str = "0",
    intention: str = Query(None),
    age_min: int = Query(18, ge=18, le=99),
    age_max: int = Query(100, ge=19, le=100),
    city: str = Query(None),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if action not in ("like", "dislike"):
        return JSONResponse({"error": "Invalid action"}, status_code=400)

    if target_id == user.id:
        return JSONResponse({"error": "Cannot swipe yourself"}, status_code=400)

    result = await db.execute(
        select(User).options(selectinload(User.profile)).where(User.id == target_id, User.is_active == True)
    )
    target = result.scalar_one_or_none()
    if not target:
        return JSONResponse({"error": "User not found"}, status_code=404)

    matched = False
    _cached_daily_super: int | None = None  # lazily populated to avoid double DB query
    result = await db.execute(
        select(Like).where(Like.liker_id == user.id, Like.liked_id == target_id)
    )
    existing = result.scalar_one_or_none()
    if not existing:
        is_super_like = (is_super == "1" and action == "like")
        if is_super_like and not user.is_premium_active:
            _cached_daily_super = await _super_likes_today(user.id, db)
        else:
            _cached_daily_super = 0
        daily_super = _cached_daily_super
        if is_super_like and not user.is_premium_active and daily_super >= 5:
            return JSONResponse({"error": "Daily super-like limit reached (5/day)", "limit": True}, status_code=429)

        like = Like(liker_id=user.id, liked_id=target_id, is_like=(action == "like"), is_super=is_super_like)
        db.add(like)
        like_committed = False
        try:
            await db.commit()
            like_committed = True
        except IntegrityError:
            await db.rollback()

        # Check for mutual like and create match regardless of whether this was a new
        # Like or a duplicate (IntegrityError) — handles double-tap race condition
        if action == "like":
            result = await db.execute(
                select(Like).where(
                    Like.liker_id == target_id,
                    Like.liked_id == user.id,
                    Like.is_like == True,
                )
            )
            mutual = result.scalar_one_or_none()
            if mutual:
                u1_id, u2_id = min(user.id, target_id), max(user.id, target_id)
                result = await db.execute(
                    select(Match).where(Match.user1_id == u1_id, Match.user2_id == u2_id)
                )
                existing_match = result.scalar_one_or_none()
                if existing_match:
                    matched = True
                else:
                    match = Match(user1_id=u1_id, user2_id=u2_id)
                    db.add(match)
                    try:
                        await db.commit()
                        await db.refresh(match)
                        matched = True
                        if is_smtp_configured() and like_committed:
                            user_name = user.profile.name if user.profile else "Someone"
                            target_name = target.profile.name if target.profile else "Someone"
                            background_tasks.add_task(
                                send_match_email, target.email, user_name,
                                lang=target.language or "en"
                            )
                            background_tasks.add_task(
                                send_match_email, user.email, target_name,
                                lang=user.language or "en"
                            )
                    except IntegrityError:
                        await db.rollback()
                        matched = True

    _intention = intention if intention and intention in VALID_INTENTIONS else None
    _city = city.strip() if city and city.strip() else None
    _online_only = request.query_params.get("online_only") == "1"
    if age_min > age_max:
        age_min, age_max = 18, 100

    lang = get_lang(request, user)
    next_cand = await find_next_candidate(
        user, db, intention=_intention, age_min=age_min, age_max=age_max,
        city=_city, online_only=_online_only,
    )
    next_data = await _candidate_to_json(next_cand, db, lang) if next_cand else None
    # Reuse cached count if already fetched; otherwise query once
    _daily = _cached_daily_super if _cached_daily_super is not None else await _super_likes_today(user.id, db)
    super_likes_left = max(0, (999 if user.is_premium_active else 5) - _daily)

    result = await db.execute(
        select(Like).where(Like.liker_id == user.id).order_by(Like.id.desc()).limit(1)
    )
    last_like = result.scalar_one_or_none()

    return JSONResponse({
        "matched": matched,
        "next": next_data,
        "super_likes_left": super_likes_left,
        "can_undo": last_like is not None and user.is_premium_active,
    })
