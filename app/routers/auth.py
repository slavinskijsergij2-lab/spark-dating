import secrets

from fastapi import APIRouter, Depends, Form, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.auth import create_access_token, hash_password, verify_password, get_optional_user
from app.database import get_db
from app.email_utils import is_smtp_configured, send_verification_email
from app.i18n import get_lang, get_translations, is_rtl
from app.models.models import User
from app.templates import templates

router = APIRouter()


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, user=Depends(get_optional_user)):
    if user:
        return RedirectResponse("/swipe", status_code=302)
    lang = get_lang(request)
    return templates.TemplateResponse("login.html", {
        "request": request,
        "t": get_translations(lang),
        "rtl": is_rtl(lang),
        "not_verified": request.query_params.get("not_verified", ""),
        "resent": request.query_params.get("resent", ""),
    })


@router.post("/login")
def login(
    request: Request,
    response: Response,
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    user = db.query(User).filter(User.email == email.lower().strip()).first()
    if not user or not verify_password(password, user.hashed_password):
        lang = user.language if user else "en"
        t = get_translations(lang)
        return templates.TemplateResponse("login.html", {
            "request": request,
            "t": t,
            "rtl": is_rtl(lang),
            "error": t.get("login_wrong", "Incorrect email or password"),
            "not_verified": "",
            "resent": "",
        }, status_code=400)

    if not user.email_verified and is_smtp_configured():
        return RedirectResponse(
            f"/login?not_verified=1&email={user.email}",
            status_code=302,
        )

    token = create_access_token(user.id)
    lang = user.language or "en"
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
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    language: str = Form("en"),
    db: Session = Depends(get_db),
):
    email = email.lower().strip()

    allowed_languages = {"ru", "uk", "de", "en", "tr", "ar"}
    if language not in allowed_languages:
        language = "en"

    t = get_translations(language)

    if db.query(User).filter(User.email == email).first():
        return templates.TemplateResponse("register.html", {
            "request": request,
            "t": t,
            "rtl": is_rtl(language),
            "error": t.get("register_email_taken", "Email already registered"),
        }, status_code=400)

    if len(password) < 6:
        return templates.TemplateResponse("register.html", {
            "request": request,
            "t": t,
            "rtl": is_rtl(language),
            "error": t.get("register_password_short", "Password must be at least 6 characters"),
        }, status_code=400)

    smtp_active = is_smtp_configured()
    verify_token = secrets.token_urlsafe(32) if smtp_active else None

    user = User(
        email=email,
        hashed_password=hash_password(password),
        language=language,
        email_verified=not smtp_active,
        email_verify_token=verify_token,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    if smtp_active:
        send_verification_email(email, verify_token, lang=language)
        return RedirectResponse("/register/check-email", status_code=302)

    # SMTP not configured — log in directly
    token = create_access_token(user.id)
    redirect = RedirectResponse("/profile/edit", status_code=302)
    redirect.set_cookie("access_token", token, httponly=True, max_age=60 * 60 * 24 * 7, samesite="lax")
    redirect.set_cookie("lang", language, max_age=60 * 60 * 24 * 365, samesite="lax")
    return redirect


@router.get("/register/check-email", response_class=HTMLResponse)
def check_email_page(request: Request):
    lang = get_lang(request)
    return templates.TemplateResponse("check_email.html", {
        "request": request,
        "t": get_translations(lang),
        "rtl": is_rtl(lang),
    })


@router.get("/verify-email/{token}", response_class=HTMLResponse)
def verify_email(token: str, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email_verify_token == token).first()
    if not user:
        return HTMLResponse(
            "<h2 style='font-family:sans-serif;text-align:center;margin-top:80px;color:#ef4444;'>"
            "❌ Invalid or expired verification link.</h2>",
            status_code=400,
        )

    user.email_verified = True
    user.email_verify_token = None
    db.commit()

    access_token = create_access_token(user.id)
    lang = user.language or "en"
    redirect = RedirectResponse("/profile/edit", status_code=302)
    redirect.set_cookie("access_token", access_token, httponly=True, max_age=60 * 60 * 24 * 7, samesite="lax")
    redirect.set_cookie("lang", lang, max_age=60 * 60 * 24 * 365, samesite="lax")
    return redirect


@router.post("/resend-verification")
def resend_verification(
    request: Request,
    email: str = Form(...),
    db: Session = Depends(get_db),
):
    email = email.lower().strip()
    user = db.query(User).filter(User.email == email).first()

    if user and not user.email_verified:
        new_token = secrets.token_urlsafe(32)
        user.email_verify_token = new_token
        db.commit()
        send_verification_email(email, new_token, lang=user.language or "en")

    # Always redirect to avoid enumeration
    return RedirectResponse(f"/login?not_verified=1&email={email}&resent=1", status_code=302)


@router.post("/logout")
def logout():
    redirect = RedirectResponse("/login", status_code=302)
    redirect.delete_cookie("access_token")
    redirect.delete_cookie("lang")
    return redirect
