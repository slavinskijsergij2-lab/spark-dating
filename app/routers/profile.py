import json
import os
from datetime import datetime

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import and_, func as _func, or_, select, update as _update
from sqlalchemy.exc import IntegrityError as _IE
from sqlalchemy.ext.asyncio import AsyncSession
from PIL import Image
Image.MAX_IMAGE_PIXELS = 25_000_000  # ~5000×5000 — blocks decompression bomb attacks
try:
    import pillow_heif
    pillow_heif.register_heif_opener()
except Exception:
    pass

from app.auth import get_current_user, hash_password, verify_password
from app.csrf import validate_csrf_form
from app.database import get_db
from app.rate_limit import rate_limit
from app.i18n import get_lang, get_translations, is_rtl, VALID_LANGS
from app.models.models import (
    Block, Match, Message, Profile, ProfilePhoto, ProfileView,
    QuizAnswer, Story, User, GenderEnum,
)
from app.templates import templates
from app.utils.time import utcnow as _utcnow

router = APIRouter()

MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_NAME_LEN = 100
MAX_BIO_LEN = 1000
MAX_CITY_LEN = 100
VALID_INTENTIONS = {"serious", "casual", "today", "browsing"}



async def save_photo(file: UploadFile) -> str:
    from app.utils.photos import save_image_bytes
    raw = await file.read(MAX_FILE_BYTES + 1)
    if len(raw) > MAX_FILE_BYTES:
        raise HTTPException(400, "photo_size_error")
    try:
        return save_image_bytes(raw, prefix="profile_")
    except ValueError:
        raise HTTPException(400, "photo_process_error")


@router.get("/profile/edit", response_class=HTMLResponse, dependencies=[Depends(rate_limit(30, 60))])
async def edit_profile_page(request: Request, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Profile).where(Profile.user_id == user.id))
    profile = result.scalar_one_or_none()
    lang = get_lang(request, user)
    verified_flash = request.query_params.get("verified") == "1"
    saved_flash = request.query_params.get("saved") == "1"
    error_flash = request.query_params.get("error", "")
    extra_photos = []
    if profile:
        result = await db.execute(
            select(ProfilePhoto).where(ProfilePhoto.profile_id == profile.id).order_by(ProfilePhoto.position)
        )
        extra_photos = result.scalars().all()
    return templates.TemplateResponse(request, "profile_edit.html", {
        "user": user,
        "profile": profile,
        "extra_photos": extra_photos,
        "photo_limit": request.query_params.get("photo_limit") == "1",
        "genders": [g.value for g in GenderEnum],
        "t": get_translations(lang),
        "lang": lang,
        "rtl": is_rtl(lang),
        "verified_flash": verified_flash,
        "saved_flash": saved_flash,
        "error": error_flash,
        "now": _utcnow,
    })


@router.post("/profile/edit", dependencies=[Depends(validate_csrf_form), Depends(rate_limit(20, 60))])
async def edit_profile(
    request: Request,
    name: str = Form(...),
    age: str = Form(...),
    gender: str = Form(...),
    looking_for: str = Form(None),
    city: str = Form(None),
    bio: str = Form(None),
    photo: UploadFile = File(None),
    language: str = Form(None),
    intention: str = Form(None),
    interests: str = Form(None),
    birth_date: str = Form(None),
    is_anonymous: str = Form(None),
    # Germany geo fields (optional; sent by city autocomplete widget)
    location_id: str = Form(None),
    geo_lat: str = Form(None),
    geo_lon: str = Form(None),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    lang = get_lang(request, user)
    t = get_translations(lang)

    async def _err(msg: str):
        result = await db.execute(select(Profile).where(Profile.user_id == user.id))
        profile = result.scalar_one_or_none()
        extra_photos = []
        if profile:
            result2 = await db.execute(
                select(ProfilePhoto).where(ProfilePhoto.profile_id == profile.id).order_by(ProfilePhoto.position)
            )
            extra_photos = result2.scalars().all()
        return templates.TemplateResponse(request, "profile_edit.html", {
            "user": user, "profile": profile,
            "extra_photos": extra_photos, "photo_limit": False,
            "genders": [g.value for g in GenderEnum],
            "t": t, "lang": lang, "rtl": is_rtl(lang),
            "verified_flash": False, "error": msg,
        }, status_code=400)

    name_val = name.strip()[:MAX_NAME_LEN] if name else ""
    bio_val = bio.strip()[:MAX_BIO_LEN] if bio else None
    city_val = city.strip()[:MAX_CITY_LEN] if city else None

    if name is not None and not name_val:
        return await _err(t.get("profile_name_required", "Name cannot be blank"))

    age_int = None
    if age and age.strip():
        try:
            age_int = int(float(age.strip()))
            if age_int < 18 or age_int > 100:
                age_int = None
        except (ValueError, TypeError):
            age_int = None

    valid_genders = [g.value for g in GenderEnum]
    gender_val = gender if gender in valid_genders else None

    result = await db.execute(select(Profile).where(Profile.user_id == user.id))
    profile = result.scalar_one_or_none()
    is_new_profile = profile is None
    if is_new_profile:
        profile = Profile(
            user_id=user.id,
            name=name_val if name_val else "—",
            age=age_int if age_int is not None else 18,
            gender=GenderEnum(gender_val) if gender_val else GenderEnum.other,
        )
        db.add(profile)
    else:
        if name_val:
            profile.name = name_val
        if age_int is not None:
            profile.age = age_int
        if gender_val:
            profile.gender = GenderEnum(gender_val)

    profile.looking_for = GenderEnum(looking_for) if looking_for and looking_for in valid_genders else None
    profile.city = city_val or None
    profile.bio = bio_val or None

    # Save Germany geo data when autocomplete was used
    try:
        _loc_id = int(location_id) if location_id and location_id.strip() else None
        _lat = float(geo_lat) if geo_lat and geo_lat.strip() else None
        _lon = float(geo_lon) if geo_lon and geo_lon.strip() else None
    except (ValueError, TypeError):
        _loc_id = _lat = _lon = None
    if _loc_id is not None:
        profile.location_id = _loc_id
        profile.lat = _lat
        profile.lon = _lon
    elif city_val is None:
        # User cleared the city field — clear geo too
        profile.location_id = None
        profile.lat = None
        profile.lon = None

    if intention and intention in VALID_INTENTIONS:
        profile.intention = intention
    elif not intention:
        profile.intention = None

    if interests is not None:
        tags = [t.strip()[:40] for t in interests.split(",") if t.strip()]
        profile.interests = ",".join(tags)[:500] or None

    profile.is_anonymous = (is_anonymous == "1")

    if photo and photo.filename:
        profile.photo = await save_photo(photo)

    if birth_date and birth_date.strip():
        try:
            user.birth_date = datetime.strptime(birth_date.strip(), "%Y-%m-%d")
        except ValueError:
            pass

    new_lang = user.language or "ru"
    if language and language in VALID_LANGS:
        user.language = language
        new_lang = language

    await db.commit()

    dest = "/swipe" if is_new_profile else "/profile/edit?saved=1"
    redirect = RedirectResponse(dest, status_code=302)
    redirect.set_cookie("lang", new_lang, max_age=60 * 60 * 24 * 365, samesite="lax")
    return redirect


@router.post("/profile/photos/add", dependencies=[Depends(validate_csrf_form), Depends(rate_limit(10, 60))])
async def add_photo(
    photo: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Profile).where(Profile.user_id == user.id))
    profile = result.scalar_one_or_none()
    if not profile:
        return RedirectResponse("/profile/edit", status_code=302)

    result = await db.execute(
        select(_func.count(ProfilePhoto.id)).where(ProfilePhoto.profile_id == profile.id)
    )
    existing_count = result.scalar() or 0
    if existing_count >= 5:
        return RedirectResponse("/profile/edit?photo_limit=1", status_code=302)

    if photo and photo.filename:
        url = await save_photo(photo)
        p = ProfilePhoto(profile_id=profile.id, url=url, position=existing_count)
        db.add(p)
        await db.commit()

    return RedirectResponse("/profile/edit", status_code=302)


@router.post("/profile/photos/delete/{photo_id}", dependencies=[Depends(validate_csrf_form), Depends(rate_limit(20, 60))])
async def delete_photo(
    photo_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Profile).where(Profile.user_id == user.id))
    profile = result.scalar_one_or_none()
    if profile:
        result = await db.execute(
            select(ProfilePhoto).where(
                ProfilePhoto.id == photo_id,
                ProfilePhoto.profile_id == profile.id,
            )
        )
        photo_obj = result.scalar_one_or_none()
        if photo_obj:
            await db.delete(photo_obj)
            await db.commit()
    return RedirectResponse("/profile/edit", status_code=302)


@router.post("/account/delete", dependencies=[Depends(validate_csrf_form), Depends(rate_limit(5, 60))])
async def delete_account(
    request: Request,
    confirm: str = Form(""),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if confirm.strip().upper() != "DELETE":
        return RedirectResponse("/profile/edit?error=type_delete_to_confirm", status_code=302)

    # Delete profile photos from filesystem
    result = await db.execute(select(Profile).where(Profile.user_id == user.id))
    profile = result.scalar_one_or_none()
    if profile:
        from app.utils.photos import remove_photo_file
        remove_photo_file(profile.photo)
        result2 = await db.execute(
            select(ProfilePhoto).where(ProfilePhoto.profile_id == profile.id)
        )
        for ph in result2.scalars().all():
            remove_photo_file(ph.url)

    await db.delete(user)
    await db.commit()

    resp = RedirectResponse("/", status_code=302)
    resp.delete_cookie("access_token")
    return resp


# ── Change password ───────────────────────────────────────────────────────────

@router.get("/settings/password", response_class=HTMLResponse, dependencies=[Depends(rate_limit(20, 60))])
async def change_password_page(
    request: Request,
    user: User = Depends(get_current_user),
):
    lang = get_lang(request, user)
    t = get_translations(lang)
    return templates.TemplateResponse(request, "settings_password.html", {
        "request": request,
        "user": user,
        "t": t,
        "lang": lang,
        "rtl": is_rtl(lang),
        "error": request.query_params.get("error"),
        "saved": request.query_params.get("saved") == "1",
    })


@router.post("/settings/password", dependencies=[Depends(validate_csrf_form), Depends(rate_limit(5, 300))])
async def change_password(
    current_password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if not verify_password(current_password, user.hashed_password):
        return RedirectResponse("/settings/password?error=wrong_current", status_code=302)
    if len(new_password) < 8:
        return RedirectResponse("/settings/password?error=too_short", status_code=302)
    if not any(c.isdigit() for c in new_password):
        return RedirectResponse("/settings/password?error=no_digit", status_code=302)
    if new_password != confirm_password:
        return RedirectResponse("/settings/password?error=no_match", status_code=302)
    if current_password == new_password:
        return RedirectResponse("/settings/password?error=same_password", status_code=302)

    new_version = (user.token_version or 0) + 1
    await db.execute(
        _update(User)
        .where(User.id == user.id)
        .values(
            hashed_password=hash_password(new_password),
            token_version=new_version,
        )
    )
    await db.commit()

    # Re-issue JWT so the current session stays valid with the new token_version
    from app.auth import create_access_token
    _secure = bool(os.getenv("RAILWAY_ENVIRONMENT"))
    resp = RedirectResponse("/settings/password?saved=1", status_code=302)
    resp.set_cookie(
        "access_token",
        create_access_token(user.id, new_version),
        httponly=True,
        samesite="lax",
        secure=_secure,
        max_age=60 * 60 * 24 * 7,
    )
    return resp


# ── GDPR data export ──────────────────────────────────────────────────────────

@router.get("/account/export", dependencies=[Depends(rate_limit(3, 3600))])
async def export_account_data(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Profile).where(Profile.user_id == user.id))
    profile = result.scalar_one_or_none()

    profile_data = None
    gallery: list = []
    if profile:
        profile_data = {
            "name": profile.name,
            "age": profile.age,
            "gender": profile.gender.value if profile.gender else None,
            "looking_for": profile.looking_for,
            "bio": profile.bio,
            "city": profile.city,
            "intention": profile.intention,
        }
        r2 = await db.execute(
            select(ProfilePhoto).where(ProfilePhoto.profile_id == profile.id)
        )
        gallery = [{"url": ph.url, "position": ph.position} for ph in r2.scalars()]

    r_quiz = await db.execute(select(QuizAnswer).where(QuizAnswer.user_id == user.id))
    quiz = [{"question_id": qa.question_id, "answer_index": qa.answer_index}
            for qa in r_quiz.scalars()]

    r_msg = await db.execute(
        select(Message)
        .where(Message.sender_id == user.id)
        .order_by(Message.created_at)
    )
    messages = [
        {"id": m.id, "match_id": m.match_id, "content": m.content,
         "created_at": m.created_at.isoformat()}
        for m in r_msg.scalars()
    ]

    r_match = await db.execute(
        select(Match).where(or_(Match.user1_id == user.id, Match.user2_id == user.id))
    )
    matches = [
        {"id": m.id,
         "partner_id": m.user2_id if m.user1_id == user.id else m.user1_id,
         "created_at": m.created_at.isoformat()}
        for m in r_match.scalars()
    ]

    r_block = await db.execute(select(Block).where(Block.blocker_id == user.id))
    blocks = [{"blocked_user_id": b.blocked_id} for b in r_block.scalars()]

    r_story = await db.execute(select(Story).where(Story.user_id == user.id))
    stories = [{"content": s.content, "created_at": s.created_at.isoformat()}
               for s in r_story.scalars()]

    payload = {
        "exported_at": _utcnow().isoformat() + "Z",
        "user": {
            "id": user.id,
            "email": user.email,
            "created_at": user.created_at.isoformat() if user.created_at else None,
            "email_verified": user.email_verified,
            "is_premium": user.is_premium,
            "language": user.language,
        },
        "profile": profile_data,
        "gallery": gallery,
        "quiz_answers": quiz,
        "messages_sent": messages,
        "matches": matches,
        "blocks": blocks,
        "stories": stories,
    }

    return Response(
        content=json.dumps(payload, ensure_ascii=False, indent=2),
        media_type="application/json",
        headers={
            "Content-Disposition": 'attachment; filename="spark-data-export.json"',
            "Cache-Control": "no-store",
        },
    )


@router.get("/settings/notifications", response_class=HTMLResponse, dependencies=[Depends(rate_limit(30, 60))])
async def notifications_settings_page(
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    lang = get_lang(request, user)
    saved = request.query_params.get("saved") == "1"
    return templates.TemplateResponse(request, "settings_notifications.html", {
        "user": user,
        "t": get_translations(lang),
        "rtl": is_rtl(lang),
        "lang": lang,
        "saved": saved,
    })


@router.post("/settings/notifications", dependencies=[Depends(validate_csrf_form), Depends(rate_limit(10, 60))])
async def notifications_settings_save(
    request: Request,
    notif_matches: str = Form(None),
    notif_messages: str = Form(None),
    notif_likes: str = Form(None),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    user.notif_matches = notif_matches == "1"
    user.notif_messages = notif_messages == "1"
    user.notif_likes = notif_likes == "1"
    await db.commit()
    return RedirectResponse("/settings/notifications?saved=1", status_code=302)


@router.get("/profile/{user_id}", response_class=HTMLResponse, dependencies=[Depends(rate_limit(60, 60))])
async def view_profile(user_id: int, request: Request, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    from sqlalchemy.orm import selectinload
    result = await db.execute(
        select(User).options(selectinload(User.profile)).where(User.id == user_id, User.is_active == True)
    )
    target = result.scalar_one_or_none()
    if not target or not target.profile:
        raise HTTPException(404, "Profile not found")
    if target.profile.is_anonymous and user.id != user_id:
        raise HTTPException(404, "Profile not found")
    lang = get_lang(request, user)

    if user.id != user_id:
        result = await db.execute(
            select(ProfileView).where(ProfileView.viewer_id == user.id, ProfileView.viewed_id == user_id)
        )
        existing = result.scalar_one_or_none()
        if existing:
            existing.created_at = _utcnow()
        else:
            db.add(ProfileView(viewer_id=user.id, viewed_id=user_id))
        try:
            await db.commit()
        except _IE:
            await db.rollback()
            await db.execute(
                _update(ProfileView)
                .where(ProfileView.viewer_id == user.id, ProfileView.viewed_id == user_id)
                .values(created_at=_utcnow())
            )
            await db.commit()
        except Exception:
            await db.rollback()

    result = await db.execute(
        select(ProfilePhoto).where(ProfilePhoto.profile_id == target.profile.id).order_by(ProfilePhoto.position)
    )
    extra_photos = result.scalars().all()

    result = await db.execute(
        select(Match.id).where(
            or_(
                and_(Match.user1_id == user.id, Match.user2_id == user_id),
                and_(Match.user1_id == user_id, Match.user2_id == user.id),
            )
        )
    )
    is_matched = user.id == user_id or bool(result.scalar_one_or_none())

    from app.utils.zodiac import get_sign, compatibility as zodiac_compat_fn
    zodiac = None
    zodiac_compat = None
    if target.birth_date:
        zodiac = get_sign(target.birth_date)
        if user.birth_date:
            my_sign = get_sign(user.birth_date)
            zodiac_compat = zodiac_compat_fn(my_sign, zodiac)

    interests_list = [t.strip() for t in (target.profile.interests or "").split(",") if t.strip()]

    from app.utils.achievements import get_achievements
    achievements = await get_achievements(target, db, lang=lang)

    return templates.TemplateResponse(request, "profile_view.html", {
        "user": user,
        "target": target,
        "profile": target.profile,
        "extra_photos": extra_photos,
        "is_matched": is_matched,
        "zodiac": zodiac,
        "zodiac_compat": zodiac_compat,
        "interests_list": interests_list,
        "achievements": achievements,
        "t": get_translations(lang),
        "rtl": is_rtl(lang),
        "lang": lang,
    })
