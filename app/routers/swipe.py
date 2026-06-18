from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_, not_

from app.auth import get_current_user
from app.database import get_db
from app.i18n import get_lang, get_translations, is_rtl
from app.models.models import Like, Match, Profile, ProfilePhoto, User, GenderEnum
from app.templates import templates

router = APIRouter()

VALID_INTENTIONS = {"serious", "casual", "today", "browsing"}


def find_next_candidate(user: User, db: Session, intention: str = None):
    profile = db.query(Profile).filter(Profile.user_id == user.id).first()
    if not profile:
        return None

    already_seen = db.query(Like.liked_id).filter(Like.liker_id == user.id).subquery()

    q = (
        db.query(User)
        .join(Profile, Profile.user_id == User.id)
        .filter(User.id != user.id)
        .filter(not_(User.id.in_(already_seen)))
    )

    if profile.looking_for:
        q = q.filter(Profile.gender == profile.looking_for)

    # Function 5: Filter by intention
    if intention and intention in VALID_INTENTIONS:
        q = q.filter(Profile.intention == intention)

    # Function 3: Sort by politeness score (higher = shown first), then by id
    q = q.order_by(User.politeness_score.desc(), User.id)

    return q.first()


@router.get("/swipe", response_class=HTMLResponse)
def swipe_page(
    request: Request,
    intention: str = Query(None),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not user.profile:
        return RedirectResponse("/profile/edit", status_code=302)

    # Normalise intention param
    if intention and intention not in VALID_INTENTIONS:
        intention = None

    candidate = find_next_candidate(user, db, intention=intention)
    lang = get_lang(request, user)
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
    })


@router.post("/swipe/{target_id}")
def do_swipe(
    target_id: int,
    action: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if action not in ("like", "dislike"):
        return JSONResponse({"error": "Invalid action"}, status_code=400)

    existing = db.query(Like).filter(Like.liker_id == user.id, Like.liked_id == target_id).first()
    if existing:
        return JSONResponse({"matched": False})

    like = Like(liker_id=user.id, liked_id=target_id, is_like=(action == "like"))
    db.add(like)
    db.commit()

    matched = False
    if action == "like":
        mutual = db.query(Like).filter(
            Like.liker_id == target_id,
            Like.liked_id == user.id,
            Like.is_like == True,
        ).first()
        if mutual:
            existing_match = db.query(Match).filter(
                or_(
                    and_(Match.user1_id == user.id, Match.user2_id == target_id),
                    and_(Match.user1_id == target_id, Match.user2_id == user.id),
                )
            ).first()
            if not existing_match:
                match = Match(user1_id=user.id, user2_id=target_id)
                db.add(match)
                db.commit()
                db.refresh(match)
                matched = True

    return JSONResponse({"matched": matched})
