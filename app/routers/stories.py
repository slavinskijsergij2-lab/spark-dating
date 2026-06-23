import base64
import io
from datetime import timedelta

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from PIL import Image
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.auth import get_current_user
from app.csrf import validate_csrf_form, validate_csrf_header
from app.database import get_db
from app.rate_limit import rate_limit
from app.utils.time import utcnow as _utcnow
from app.models.models import Match, Story, User
from app.templates import templates

router = APIRouter()

STORY_TTL_HOURS = 24
MAX_STORY_IMG_BYTES = 5 * 1024 * 1024


def _active_stories_stmt():
    """Return a base SELECT statement for non-expired stories."""
    return select(Story).where(Story.expires_at > _utcnow())


@router.post("/stories", dependencies=[Depends(validate_csrf_form), Depends(rate_limit(5, 60))])
async def create_story(
    request: Request,
    text: str = Form(None),
    photo: UploadFile = File(None),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
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
        except Exception:
            return JSONResponse({"error": "Invalid image"}, status_code=400)
        data = base64.b64encode(buf.getvalue()).decode()
        content = f"data:image/jpeg;base64,{data}"
        media_type = "image"
    elif text and text.strip():
        content = text.strip()[:300]
        media_type = "text"
    else:
        return JSONResponse({"error": "Provide text or photo"}, status_code=400)

    result = await db.execute(
        _active_stories_stmt().where(Story.user_id == user.id)
    )
    existing = result.scalar_one_or_none()
    if existing:
        existing.content = content
        existing.media_type = media_type
        existing.expires_at = expires
        await db.commit()
        await db.refresh(existing)
        return JSONResponse({"success": True, "id": existing.id})

    story = Story(user_id=user.id, content=content, media_type=media_type, expires_at=expires)
    db.add(story)
    try:
        await db.commit()
        await db.refresh(story)
    except IntegrityError:
        # Race: another concurrent request already inserted — update it instead
        await db.rollback()
        result2 = await db.execute(select(Story).where(Story.user_id == user.id))
        story = result2.scalar_one()
        story.content = content
        story.media_type = media_type
        story.expires_at = expires
        await db.commit()
        await db.refresh(story)
    return JSONResponse({"success": True, "id": story.id})


@router.delete("/stories/{story_id}", dependencies=[Depends(validate_csrf_header), Depends(rate_limit(10, 60))])
async def delete_story(story_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Story).where(Story.id == story_id, Story.user_id == user.id)
    )
    story = result.scalar_one_or_none()
    if story:
        await db.delete(story)
        await db.commit()
    return JSONResponse({"success": True})


@router.get("/stories/feed", dependencies=[Depends(rate_limit(30, 60))])
async def stories_feed(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Stories from my matches + my own."""
    result = await db.execute(
        select(Match).where(or_(Match.user1_id == user.id, Match.user2_id == user.id))
    )
    my_matches = result.scalars().all()
    partner_ids = set()
    for m in my_matches:
        partner_ids.add(m.user2_id if m.user1_id == user.id else m.user1_id)
    partner_ids.add(user.id)

    result = await db.execute(
        _active_stories_stmt()
        .where(Story.user_id.in_(partner_ids))
        .order_by(Story.created_at.desc())
    )
    stories = result.scalars().all()

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

    user_ids = list(by_user.keys())
    result = await db.execute(
        select(User).options(joinedload(User.profile)).where(User.id.in_(user_ids))
    )
    users_map = {u.id: u for u in result.scalars().unique().all()}

    feed = []
    for uid, slist in by_user.items():
        u = users_map.get(uid)
        if not u:
            continue
        feed.append({
            "user_id": uid,
            "name": u.profile.name if u.profile else "?",
            "photo": u.profile.photo if u.profile else None,
            "is_me": uid == user.id,
            "stories": slist,
        })

    return JSONResponse(feed)


@router.get("/stories/page", response_class=HTMLResponse)
async def stories_page(request: Request, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    from app.i18n import get_lang, get_translations, is_rtl
    lang = get_lang(request, user)
    result = await db.execute(
        _active_stories_stmt().where(Story.user_id == user.id)
    )
    my_story = result.scalar_one_or_none()
    return templates.TemplateResponse(request, "stories.html", {
        "user": user,
        "my_story": my_story,
        "t": get_translations(lang),
        "rtl": is_rtl(lang),
        "lang": lang,
    })
