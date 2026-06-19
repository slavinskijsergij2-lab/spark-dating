import base64
import io
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session
from PIL import Image

from app.auth import get_current_user
from app.database import get_db
from app.i18n import get_lang, get_translations, is_rtl, VALID_LANGS
from app.models.models import Profile, ProfilePhoto, User, GenderEnum
from app.templates import templates

router = APIRouter()

MAX_SIZE = (800, 800)

VALID_INTENTIONS = {"serious", "casual", "today", "browsing"}


def save_photo(file: UploadFile) -> str:
    """Returns a data: URI so photos survive container restarts."""
    ext = Path(file.filename).suffix.lower()
    if ext not in {".jpg", ".jpeg", ".png", ".webp"}:
        raise HTTPException(400, "Только JPG/PNG/WEBP изображения")
    try:
        img = Image.open(file.file)
        img.thumbnail(MAX_SIZE, Image.LANCZOS)
        img = img.convert("RGB")
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=80)
    except Exception:
        raise HTTPException(400, "Не удалось обработать изображение. Попробуйте другой файл.")
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


@router.post("/profile/edit")
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

    try:
        age_int = int(float(age))
    except (ValueError, TypeError):
        return _err(t.get("age_invalid", "Введите корректный возраст"))

    if age_int < 18 or age_int > 100:
        return _err(t.get("age_range", "Возраст должен быть от 18 до 100"))
    if gender not in [g.value for g in GenderEnum]:
        return _err(t.get("gender_invalid", "Выберите пол"))

    profile = db.query(Profile).filter(Profile.user_id == user.id).first()
    is_new_profile = profile is None
    if is_new_profile:
        profile = Profile(user_id=user.id)
        db.add(profile)

    profile.name = name.strip()
    profile.age = age_int
    profile.gender = GenderEnum(gender)
    profile.looking_for = GenderEnum(looking_for) if looking_for else None
    profile.city = city.strip() if city else None
    profile.bio = bio.strip() if bio else None

    if intention and intention in VALID_INTENTIONS:
        profile.intention = intention
    elif not intention:
        profile.intention = None

    if photo and photo.filename:
        profile.photo = save_photo(photo)

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


@router.post("/profile/photos/add")
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
        url = save_photo(photo)
        p = ProfilePhoto(profile_id=profile.id, url=url, position=existing)
        db.add(p)
        db.commit()

    return RedirectResponse("/profile/edit", status_code=302)


@router.post("/profile/photos/delete/{photo_id}")
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
        raise HTTPException(404, "Профиль не найден")
    lang = get_lang(request, user)
    extra_photos = db.query(ProfilePhoto).filter(ProfilePhoto.profile_id == target.profile.id).order_by(ProfilePhoto.position).all()
    return templates.TemplateResponse("profile_view.html", {
        "request": request,
        "user": user,
        "target": target,
        "profile": target.profile,
        "extra_photos": extra_photos,
        "t": get_translations(lang),
        "rtl": is_rtl(lang),
    })
