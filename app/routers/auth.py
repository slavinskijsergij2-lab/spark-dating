from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.auth import create_access_token, hash_password, verify_password, get_optional_user
from app.database import get_db
from app.i18n import get_lang, get_translations, is_rtl
from app.models.models import User

router = APIRouter()
templates = Jinja2Templates(directory="templates")


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, user=Depends(get_optional_user)):
    if user:
        return RedirectResponse("/swipe", status_code=302)
    lang = get_lang(request)
    return templates.TemplateResponse("login.html", {
        "request": request,
        "t": get_translations(lang),
        "rtl": is_rtl(lang),
    })


@router.post("/login")
def login(
    response: Response,
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    user = db.query(User).filter(User.email == email.lower().strip()).first()
    if not user or not verify_password(password, user.hashed_password):
        raise HTTPException(status_code=400, detail="Неверный email или пароль")

    token = create_access_token(user.id)
    lang = user.language or "ru"
    redirect = RedirectResponse("/swipe", status_code=302)
    redirect.set_cookie("access_token", token, httponly=True, max_age=60 * 60 * 24 * 7, samesite="lax")
    redirect.set_cookie("lang", lang, max_age=60 * 60 * 24 * 365, samesite="lax")
    return redirect


@router.get("/register", response_class=HTMLResponse)
def register_page(request: Request, user=Depends(get_optional_user)):
    if user:
        return RedirectResponse("/swipe", status_code=302)
    lang = get_lang(request)
    return templates.TemplateResponse("register.html", {
        "request": request,
        "t": get_translations(lang),
        "rtl": is_rtl(lang),
    })


@router.post("/register")
def register(
    email: str = Form(...),
    password: str = Form(...),
    language: str = Form("en"),
    db: Session = Depends(get_db),
):
    email = email.lower().strip()
    if db.query(User).filter(User.email == email).first():
        raise HTTPException(status_code=400, detail="Email уже зарегистрирован")
    if len(password) < 6:
        raise HTTPException(status_code=400, detail="Пароль минимум 6 символов")

    allowed_languages = {"ru", "uk", "de", "en", "tr", "ar"}
    if language not in allowed_languages:
        language = "ru"

    user = User(email=email, hashed_password=hash_password(password), language=language)
    db.add(user)
    db.commit()
    db.refresh(user)

    token = create_access_token(user.id)
    redirect = RedirectResponse("/profile/edit", status_code=302)
    redirect.set_cookie("access_token", token, httponly=True, max_age=60 * 60 * 24 * 7, samesite="lax")
    redirect.set_cookie("lang", language, max_age=60 * 60 * 24 * 365, samesite="lax")
    return redirect


@router.post("/logout")
def logout():
    redirect = RedirectResponse("/login", status_code=302)
    redirect.delete_cookie("access_token")
    redirect.delete_cookie("lang")
    return redirect
