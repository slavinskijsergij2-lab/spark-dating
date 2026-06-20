import base64
import io
from datetime import timedelta

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from PIL import Image
from sqlalchemy import or_
from sqlalchemy.orm import Session, joinedload

from app.auth import get_current_user
from app.csrf import validate_csrf_form, validate_csrf_header
from app.database import get_db
from app.utils.time import utcnow as _utcnow
from app.models.models import Match, Story, User
from app.templates import templates

router = APIRouter()

STORY_TTL_HOURS = 24
MAX_STORY_IMG_BYTES = 5 * 1024 * 1024


def _active_stories(db: Session):
    """Return all non-expired stories."""
    return db.query(Story).filter(Story.expires_at > _utcnow())


@router.post("/stories", dependencies=[Depends(validate_csrf_form)])
async def create_story(
    request: Request,
    text: str = Form(None),
    photo: UploadFile = File(None),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    expires = _utcnow() + timedelta(hours=STORY_TTL_HOURS)

    if photo and photo.filename:
        raw = await photo.read(MAX_STORY_IMG_BYTES + 1)
        if len(raw) > MAX_STORY_IMG_BYTES:
            return JSONResponse({"error": "Image too large (max 5 MB)"}, status_code=400)
        try:
            img = Image.open(io.BytesIO(raw))
            img.thumbnail((600, 600), Image.LANCZOS)
            img = img.convert("RGB")
            buf = io.BytesIO()
            img.save(buf, "JPEG", quality=75)
            b64 = base64.b64encode(buf.getvalue()).decode()
            content = f"data:image/jpeg;base64,{b64}"
            media_type = "image"
        except Exception:
            return JSONResponse({"error": "Invalid image"}, status_code=400)
    elif text and text.strip():
        content = text.strip()[:300]
        media_type = "text"
    else:
        return JSONResponse({"error": "Provide text or photo"}, status_code=400)

    story = Story(user_id=user.id, content=content, media_type=media_type, expires_at=expires)
    db.add(story)
    db.commit()
    db.refresh(story)
    return JSONResponse({"success": True, "id": story.id})


@router.delete("/stories/{story_id}", dependencies=[Depends(validate_csrf_header)])
def delete_story(story_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    story = db.query(Story).filter(Story.id == story_id, Story.user_id == user.id).first()
    if story:
        db.delete(story)
        db.commit()
    return JSONResponse({"success": True})


@router.get("/stories/feed")
def stories_feed(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Stories from my matches + my own."""
    my_match_ids_q = db.query(Match).filter(
        or_(Match.user1_id == user.id, Match.user2_id == user.id)
    ).all()
    partner_ids = set()
    for m in my_match_ids_q:
        partner_ids.add(m.user2_id if m.user1_id == user.id else m.user1_id)
    partner_ids.add(user.id)

    stories = (
        _active_stories(db)
        .filter(Story.user_id.in_(partner_ids))
        .order_by(Story.created_at.desc())
        .all()
    )

    # Group by user
    by_user: dict = {}
    for s in stories:
        by_user.setdefault(s.user_id, []).append({
            "id": s.id,
            "content": s.content,
            "media_type": s.media_type,
            "created_at": s.created_at.isoformat(),
            "expires_at": s.expires_at.isoformat(),
            "is_mine": s.user_id == user.id,
        })

    # HIGH-4: batch-load all story authors in a single query (was N+1)
    user_ids = list(by_user.keys())
    users_map = {
        u.id: u
        for u in db.query(User).options(joinedload(User.profile)).filter(User.id.in_(user_ids)).all()
    }

    result = []
    for uid, slist in by_user.items():
        u = users_map.get(uid)
        if not u:
            continue
        result.append({
            "user_id": uid,
            "name": u.profile.name if u.profile else "?",
            "photo": u.profile.photo if u.profile else None,
            "is_me": uid == user.id,
            "stories": slist,
        })

    return JSONResponse(result)


@router.get("/stories/page", response_class=HTMLResponse)
def stories_page(request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    from app.i18n import get_lang, get_translations, is_rtl
    lang = get_lang(request, user)
    my_story = _active_stories(db).filter(Story.user_id == user.id).first()
    return templates.TemplateResponse("stories.html", {
        "request": request,
        "user": user,
        "my_story": my_story,
        "t": get_translations(lang),
        "rtl": is_rtl(lang),
        "lang": lang,
    })
