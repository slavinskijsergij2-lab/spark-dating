import base64
import io
import os
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError as _IE
from sqlalchemy.ext.asyncio import AsyncSession
from PIL import Image
Image.MAX_IMAGE_PIXELS = 25_000_000  # ~5000×5000 — blocks decompression bomb attacks
try:
    import pillow_heif
    pillow_heif.register_heif_opener()
except ImportError:
    pass

from app.auth import get_current_user
from app.csrf import validate_csrf_form
from app.database import get_db
from app.rate_limit import rate_limit
from app.i18n import get_lang, get_translations, is_rtl, VALID_LANGS
from app.models.models import Match, Profile, ProfilePhoto, ProfileView, User, GenderEnum
from app.templates import templates
from app.utils.time import utcnow as _utcnow

router = APIRouter()

MAX_SIZE = (800, 800)
MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_NAME_LEN = 100
MAX_BIO_LEN = 1000
MAX_CITY_LEN = 100
VALID_INTENTIONS = {"serious", "casual", "today", "browsing"}


def _photo_dir() -> Path:
    d = Path(os.getenv("PHOTO_DIR", "static/photos"))
    d.mkdir(parents=True, exist_ok=True)
    return d


async def save_photo(file: UploadFile) -> str:
    raw = await file.read(MAX_FILE_BYTES + 1)
    if len(raw) > MAX_FILE_BYTES:
        raise HTTPException(400, "photo_size_error")

    try:
        img = Image.open(io.BytesIO(raw))
        img.thumbnail(MAX_SIZE, Image.LANCZOS)
        img = img.convert("RGB")
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=80)
    except Exception:
        raise HTTPException(400, "photo_process_error")

    # Store as base64 data URL — survives Railway redeploys without a Volume.
    data = base64.b64encode(buf.getvalue()).decode()
    return f"data:image/jpeg;base64,{data}"


@router.get("/profile/edit", response_class=HTMLResponse)
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
        select(ProfilePhoto).where(ProfilePhoto.profile_id == profile.id)
    )
    existing_count = len(result.scalars().all())
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


@router.get("/profile/{user_id}", response_class=HTMLResponse)
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
                select(ProfileView).where(ProfileView.viewer_id == user.id, ProfileView.viewed_id == user_id)
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
