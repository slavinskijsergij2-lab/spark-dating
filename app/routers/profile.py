import os
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from PIL import Image

from app.auth import get_current_user
from app.database import get_db
from app.i18n import get_lang, get_translations, is_rtl, VALID_LANGS
from app.models.models import Profile, User, GenderEnum

router = APIRouter()
templates = Jinja2Templates(directory="templates")

UPLOAD_DIR = Path("static/uploads")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
MAX_SIZE = (800, 800)

VALID_INTENTIONS = {"serious", "casual", "today", "browsing"}


def save_photo(file: UploadFile) -> str:
    ext = Path(file.filename).suffix.lower()
    if ext not in {".jpg", ".jpeg", ".png", ".webp"}:
        raise HTTPException(400, "Только JPG/PNG/WEBP изображения")
    filename = f"{uuid.uuid4().hex}{ext}"
    path = UPLOAD_DIR / filename
    img = Image.open(file.file)
    img.thumbnail(MAX_SIZE, Image.LANCZOS)
    img = img.convert("RGB")
    img.save(path, "JPEG", quality=85)
    return f"/static/uploads/{filename}"


@router.get("/profile/edit", response_class=HTMLResponse)
def edit_profile_page(request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    profile = db.query(Profile).filter(Profile.user_id == user.id).first()
    lang = get_lang(request, user)
    verified_flash = request.query_params.get("verified") == "1"
    return templates.TemplateResponse("profile_edit.html", {
        "request": request,
        "user": user,
        "profile": profile,
        "genders": [g.value for g in GenderEnum],
        "t": get_translations(lang),
        "lang": lang,
        "rtl": is_rtl(lang),
        "verified_flash": verified_flash,
    })


@router.post("/profile/edit")
async def edit_profile(
    request: Request,
    name: str = Form(...),
    age: int = Form(...),
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
    if age < 18 or age > 100:
        raise HTTPException(400, "Возраст должен быть от 18 до 100")
    if gender not in [g.value for g in GenderEnum]:
        raise HTTPException(400, "Неверный пол")

    profile = db.query(Profile).filter(Profile.user_id == user.id).first()
    if not profile:
        profile = Profile(user_id=user.id)
        db.add(profile)

    profile.name = name.strip()
    profile.age = age
    profile.gender = GenderEnum(gender)
    profile.looking_for = GenderEnum(looking_for) if looking_for else None
    profile.city = city.strip() if city else None
    profile.bio = bio.strip() if bio else None

    # Function 5: Save intention
    if intention and intention in VALID_INTENTIONS:
        profile.intention = intention
    elif intention == "" or intention is None:
        # Allow clearing intention only if explicitly set to empty
        pass

    if photo and photo.filename:
        profile.photo = save_photo(photo)

    new_lang = user.language or "ru"
    if language and language in VALID_LANGS:
        user.language = language
        new_lang = language

    db.commit()

    redirect = RedirectResponse("/swipe", status_code=302)
    redirect.set_cookie("lang", new_lang, max_age=60 * 60 * 24 * 365, samesite="lax")
    return redirect


@router.get("/profile/{user_id}", response_class=HTMLResponse)
def view_profile(user_id: int, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    target = db.query(User).filter(User.id == user_id).first()
    if not target or not target.profile:
        raise HTTPException(404, "Профиль не найден")
    lang = get_lang(request, user)
    return templates.TemplateResponse("profile_view.html", {
        "request": request,
        "user": user,
        "target": target,
        "profile": target.profile,
        "t": get_translations(lang),
        "rtl": is_rtl(lang),
    })
