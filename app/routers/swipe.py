from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.orm import Session
from sqlalchemy import and_, case, or_, not_
from sqlalchemy.exc import IntegrityError

from app.auth import get_current_user
from app.csrf import validate_csrf_header
from app.utils.time import utcnow as _utcnow
from app.database import get_db
from app.email_utils import is_smtp_configured, send_match_email
from app.i18n import get_lang, get_translations, is_rtl
from app.models.models import Block, Like, Match, Profile, ProfilePhoto, User, GenderEnum
from app.templates import templates


router = APIRouter()

VALID_INTENTIONS = {"serious", "casual", "today", "browsing"}


def _super_likes_today(user_id: int, db: Session) -> int:
    from datetime import date
    today_start = _utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    return db.query(Like).filter(
        Like.liker_id == user_id,
        Like.is_super == True,
        Like.created_at >= today_start,
    ).count()


def find_next_candidate(
    user: User, db: Session,
    intention: str = None, age_min: int = 18, age_max: int = 100,
    city: str = None, online_only: bool = False,
):
    profile = db.query(Profile).filter(Profile.user_id == user.id).first()
    if not profile:
        return None

    already_seen = db.query(Like.liked_id).filter(Like.liker_id == user.id).scalar_subquery()
    now = _utcnow()

    q = (
        db.query(User)
        .join(Profile, Profile.user_id == User.id)
        .filter(User.id != user.id)
        .filter(User.is_active == True)
        .filter(not_(User.id.in_(already_seen)))
        .filter(Profile.age >= age_min)
        .filter(Profile.age <= age_max)
        # Anonymous-mode users ARE shown in swipe (photo is blurred in the card).
        # They are revealed only when both parties agree in chat.
    )

    if profile.looking_for:
        q = q.filter(Profile.gender == profile.looking_for)
        # MEDIUM-3: also require mutual compatibility — candidate must be looking for user's gender
        q = q.filter(
            or_(
                Profile.looking_for == None,
                Profile.looking_for == profile.gender,
            )
        )

    if intention and intention in VALID_INTENTIONS:
        q = q.filter(Profile.intention == intention)

    if city and city.strip():
        q = q.filter(Profile.city.ilike(f"%{city.strip()}%"))

    if online_only:
        from datetime import timedelta
        online_threshold = _utcnow() - timedelta(minutes=5)
        q = q.filter(User.last_seen >= online_threshold)

    # Exclude blocked users (both directions)
    blocked_ids = db.query(Block.blocked_id).filter(Block.blocker_id == user.id).scalar_subquery()
    blocker_ids = db.query(Block.blocker_id).filter(Block.blocked_id == user.id).scalar_subquery()
    q = q.filter(not_(User.id.in_(blocked_ids))).filter(not_(User.id.in_(blocker_ids)))

    # Boosted profiles appear first, then by politeness score
    is_boosted = case((User.boost_until > now, 1), else_=0)
    q = q.order_by(is_boosted.desc(), User.politeness_score.desc(), User.id)

    return q.first()


@router.get("/swipe", response_class=HTMLResponse)
def swipe_page(
    request: Request,
    intention: str = Query(None),
    age_min: int = Query(18, ge=18, le=99),
    age_max: int = Query(100, ge=19, le=100),
    city: str = Query(None),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not user.profile:
        return RedirectResponse("/profile/edit", status_code=302)

    if intention and intention not in VALID_INTENTIONS:
        intention = None
    if age_min > age_max:
        age_min, age_max = 18, 100

    city_filter = city.strip() if city and city.strip() else None
    online_only = request.query_params.get("online_only") == "1"
    candidate = find_next_candidate(
        user, db, intention=intention, age_min=age_min, age_max=age_max,
        city=city_filter, online_only=online_only,
    )
    lang = get_lang(request, user)
    super_likes_left = max(0, (999 if user.is_premium else 5) - _super_likes_today(user.id, db))
    last_like = db.query(Like).filter(Like.liker_id == user.id).order_by(Like.id.desc()).first()
    extra_photos = []
    if candidate and candidate.profile:
        extra_photos = db.query(ProfilePhoto).filter(
            ProfilePhoto.profile_id == candidate.profile.id
        ).order_by(ProfilePhoto.position).all()
    return templates.TemplateResponse("swipe.html", {
        "request": request,
        "user": user,
        "candidate": candidate,
        "profile": candidate.profile if candidate else None,
        "extra_photos": extra_photos,
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
        "can_undo": last_like is not None and user.is_premium,
    })


@router.post("/swipe", dependencies=[Depends(validate_csrf_header)])
def swipe_noop(user=Depends(get_current_user)):
    return JSONResponse({"matched": False})


@router.post("/swipe/undo", dependencies=[Depends(validate_csrf_header)])
def undo_swipe(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not user.is_premium:
        return JSONResponse({"error": "Premium only"}, status_code=403)
    last_like = db.query(Like).filter(Like.liker_id == user.id).order_by(Like.id.desc()).first()
    if not last_like:
        return JSONResponse({"error": "Nothing to undo"}, status_code=400)
    db.delete(last_like)
    db.commit()
    return JSONResponse({"success": True})


@router.post("/swipe/{target_id}", dependencies=[Depends(validate_csrf_header)])
def do_swipe(
    target_id: int,
    action: str,
    is_super: str = "0",
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if action not in ("like", "dislike"):
        return JSONResponse({"error": "Invalid action"}, status_code=400)

    target = db.query(User).filter(User.id == target_id, User.is_active == True).first()
    if not target:
        return JSONResponse({"error": "User not found"}, status_code=404)

    existing = db.query(Like).filter(Like.liker_id == user.id, Like.liked_id == target_id).first()
    if existing:
        return JSONResponse({"matched": False})

    is_super_like = (is_super == "1" and action == "like")
    if is_super_like and not user.is_premium:
        daily = _super_likes_today(user.id, db)
        if daily >= 5:
            return JSONResponse({"error": "Daily super-like limit reached (5/day)", "limit": True}, status_code=429)

    like = Like(liker_id=user.id, liked_id=target_id, is_like=(action == "like"), is_super=is_super_like)
    db.add(like)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return JSONResponse({"matched": False})

    matched = False
    if action == "like":
        mutual = db.query(Like).filter(
            Like.liker_id == target_id,
            Like.liked_id == user.id,
            Like.is_like == True,
        ).first()
        if mutual:
            # CRITICAL-2: Normalize user IDs so UNIQUE(user1_id, user2_id) always covers both orderings.
            # min→user1, max→user2 guarantees a single canonical row for each pair.
            u1_id, u2_id = min(user.id, target_id), max(user.id, target_id)
            existing_match = db.query(Match).filter(
                Match.user1_id == u1_id,
                Match.user2_id == u2_id,
            ).first()
            if not existing_match:
                match = Match(user1_id=u1_id, user2_id=u2_id)
                db.add(match)
                try:
                    db.commit()
                    db.refresh(match)
                    matched = True
                    if is_smtp_configured():
                        user_name = user.profile.name if user.profile else "Someone"
                        target_name = target.profile.name if target.profile else "Someone"
                        send_match_email(target.email, user_name, lang=target.language or "en")
                        send_match_email(user.email, target_name, lang=user.language or "en")
                except IntegrityError:
                    db.rollback()

    return JSONResponse({"matched": matched})
