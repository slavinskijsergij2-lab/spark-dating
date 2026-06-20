import base64
import io
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session
from PIL import Image

from app.auth import get_current_user
from app.csrf import validate_csrf_form
from app.database import get_db
from app.i18n import get_lang, get_translations, is_rtl, VALID_LANGS
from app.models.models import Match, Profile, ProfilePhoto, ProfileView, User, GenderEnum
from sqlalchemy import and_, or_
from app.templates import templates
from app.utils.time import utcnow as _utcnow

router = APIRouter()

MAX_SIZE = (800, 800)
MAX_FILE_BYTES = 10 * 1024 * 1024  # 10 MB
MAX_NAME_LEN = 100
MAX_BIO_LEN = 1000
MAX_CITY_LEN = 100
VALID_INTENTIONS = {"serious", "casual", "today", "browsing"}


async def save_photo(file: UploadFile) -> str:
    """Resize uploaded image and return as base64 data URI for persistent DB storage.
    Storing in DB (not filesystem) means photos survive Railway container restarts/redeploys."""
    ext = Path(file.filename or "x.jpg").suffix.lower()
    if ext not in {".jpg", ".jpeg", ".png", ".webp"}:
        raise HTTPException(400, "photo_format_error")

    raw = await file.read()
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

    b64 = base64.b64encode(buf.getvalue()).decode()
    return f"data:image/jpeg;base64,{b64}"


@router.get("/profile/edit", response_class=HTMLResponse)
def edit_profile_page(request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    profile = db.query(Profile).filter(Profile.user_id == user.id).first()
    lang = get_lang(request, user)
    verified_flash = request.query_params.get("verified") == "1"
    saved_flash = request.query_params.get("saved") == "1"
    error_flash = request.query_params.get("error", "")
    extra_photos = db.query(ProfilePhoto).filter(ProfilePhoto.profile_id == profile.id).order_by(ProfilePhoto.position).all() if profile else []
    return templates.TemplateResponse("profile_edit.html", {
        "request": request,
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
    })


@router.post("/profile/edit", dependencies=[Depends(validate_csrf_form)])
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
    db: Session = Depends(get_db),
):
    lang = get_lang(request, user)
    t = get_translations(lang)

    def _err(msg: str):
        profile = db.query(Profile).filter(Profile.user_id == user.id).first()
        extra_photos = db.query(ProfilePhoto).filter(ProfilePhoto.profile_id == profile.id).order_by(ProfilePhoto.position).all() if profile else []
        return templates.TemplateResponse("profile_edit.html", {
            "request": request, "user": user, "profile": profile,
            "extra_photos": extra_photos, "photo_limit": False,
            "genders": [g.value for g in GenderEnum],
            "t": t, "lang": lang, "rtl": is_rtl(lang),
            "verified_flash": False, "error": msg,
        }, status_code=400)

    # FIX M2: truncate text fields to server-enforced limits
    name_val = name.strip()[:MAX_NAME_LEN] if name else ""
    bio_val = bio.strip()[:MAX_BIO_LEN] if bio else None
    city_val = city.strip()[:MAX_CITY_LEN] if city else None

    # Age — optional, use 18 as default for new profiles
    age_int = None
    if age and age.strip():
        try:
            age_int = int(float(age.strip()))
            # LOW-20: enforce minimum legal age for a dating app
            if age_int < 18 or age_int > 100:
                age_int = None
        except (ValueError, TypeError):
            age_int = None

    # Gender — optional, default to "other" for new profiles
    valid_genders = [g.value for g in GenderEnum]
    gender_val = gender if gender in valid_genders else None

    profile = db.query(Profile).filter(Profile.user_id == user.id).first()
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

    # Interests — comma-separated tags, max 500 chars total
    if interests is not None:
        tags = [t.strip()[:40] for t in interests.split(",") if t.strip()]
        profile.interests = ",".join(tags)[:500] or None

    # Anonymous mode toggle
    profile.is_anonymous = (is_anonymous == "1")

    if photo and photo.filename:
        profile.photo = await save_photo(photo)  # FIX H5: properly awaited

    # Birth date — store on User model
    if birth_date and birth_date.strip():
        try:
            user.birth_date = datetime.strptime(birth_date.strip(), "%Y-%m-%d")
        except ValueError:
            pass

    new_lang = user.language or "ru"
    if language and language in VALID_LANGS:
        user.language = language
        new_lang = language

    db.commit()

    # First-time setup → go straight to swipe; editing → stay on form with success flash
    dest = "/swipe" if is_new_profile else "/profile/edit?saved=1"
    redirect = RedirectResponse(dest, status_code=302)
    redirect.set_cookie("lang", new_lang, max_age=60 * 60 * 24 * 365, samesite="lax")
    return redirect


@router.post("/profile/photos/add", dependencies=[Depends(validate_csrf_form)])
async def add_photo(
    photo: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    profile = db.query(Profile).filter(Profile.user_id == user.id).first()
    if not profile:
        return RedirectResponse("/profile/edit", status_code=302)

    existing = db.query(ProfilePhoto).filter(ProfilePhoto.profile_id == profile.id).count()
    if existing >= 5:
        return RedirectResponse("/profile/edit?photo_limit=1", status_code=302)

    if photo and photo.filename:
        url = await save_photo(photo)  # FIX H5: properly awaited
        p = ProfilePhoto(profile_id=profile.id, url=url, position=existing)
        db.add(p)
        db.commit()

    return RedirectResponse("/profile/edit", status_code=302)


@router.post("/profile/photos/delete/{photo_id}", dependencies=[Depends(validate_csrf_form)])
def delete_photo(
    photo_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    profile = db.query(Profile).filter(Profile.user_id == user.id).first()
    if profile:
        photo = db.query(ProfilePhoto).filter(
            ProfilePhoto.id == photo_id,
            ProfilePhoto.profile_id == profile.id,
        ).first()
        if photo:
            db.delete(photo)
            db.commit()
    return RedirectResponse("/profile/edit", status_code=302)


@router.get("/profile/{user_id}", response_class=HTMLResponse)
def view_profile(user_id: int, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    target = db.query(User).filter(User.id == user_id).first()
    if not target or not target.profile:
        raise HTTPException(404, "Profile not found")
    lang = get_lang(request, user)

    # Track profile view (don't track self-views)
    if user.id != user_id:
        from sqlalchemy.exc import IntegrityError as _IE
        existing = db.query(ProfileView).filter(
            ProfileView.viewer_id == user.id,
            ProfileView.viewed_id == user_id,
        ).first()
        if existing:
            existing.created_at = _utcnow()
        else:
            db.add(ProfileView(viewer_id=user.id, viewed_id=user_id))
        try:
            db.commit()
        except _IE:
            db.rollback()
            db.query(ProfileView).filter(
                ProfileView.viewer_id == user.id,
                ProfileView.viewed_id == user_id,
            ).update({"created_at": _utcnow()})
            db.commit()
        except Exception:
            db.rollback()
    extra_photos = db.query(ProfilePhoto).filter(ProfilePhoto.profile_id == target.profile.id).order_by(ProfilePhoto.position).all()
    is_matched = user.id == user_id or bool(
        db.query(Match.id).filter(
            or_(
                and_(Match.user1_id == user.id, Match.user2_id == user_id),
                and_(Match.user1_id == user_id, Match.user2_id == user.id),
            )
        ).first()
    )

    # Zodiac sign + compatibility
    from app.utils.zodiac import get_sign, compatibility as zodiac_compat_fn
    zodiac = None
    zodiac_compat = None
    if target.birth_date:
        zodiac = get_sign(target.birth_date)
        if user.birth_date:
            my_sign = get_sign(user.birth_date)
            zodiac_compat = zodiac_compat_fn(my_sign, zodiac)

    # Interests list
    interests_list = [t.strip() for t in (target.profile.interests or "").split(",") if t.strip()]

    # Achievements
    from app.utils.achievements import get_achievements
    achievements = get_achievements(target, db, lang=lang)

    return templates.TemplateResponse("profile_view.html", {
        "request": request,
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
    })
