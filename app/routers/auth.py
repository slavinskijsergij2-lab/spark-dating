import os
import secrets
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.auth import (
    DUMMY_HASH,
    create_access_token,
    hash_password,
    verify_password,
    get_optional_user,
)
from app.csrf import validate_csrf_form
from app.database import get_db
from app.email_utils import is_smtp_configured, send_verification_email
from app.i18n import get_lang, get_translations, is_rtl
from app.models.models import User
from app.rate_limit import rate_limit
from app.templates import templates

router = APIRouter()

_EMAIL_VERIFY_TTL_SECONDS = 86400  # 24 hours
# Secure flag for cookies: True on Railway production, False in local dev
_SECURE_COOKIES = bool(os.getenv("RAILWAY_ENVIRONMENT") or os.getenv("SECURE_COOKIES"))


from app.utils.time import utcnow as _utcnow


def _set_auth_cookie(response, token: str) -> None:
    """Set the JWT access_token cookie with correct security flags."""
    response.set_cookie(
        "access_token", token,
        httponly=True,
        max_age=60 * 60 * 24 * 7,
        samesite="lax",
        secure=_SECURE_COOKIES,
    )


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


@router.post("/login", dependencies=[Depends(rate_limit(10, 60)), Depends(validate_csrf_form)])
def login(
    request: Request,
    response: Response,
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    user = db.query(User).filter(User.email == email.lower().strip()).first()

    # FIX H1: always run bcrypt to prevent timing-based email enumeration.
    # If the user doesn't exist we check against DUMMY_HASH so the response
    # time is constant regardless of whether the email is registered.
    password_ok = verify_password(password, user.hashed_password if user else DUMMY_HASH)

    if not user or not password_ok:
        lang = (user.language if user and user.language else None) or get_lang(request)
        t = get_translations(lang)
        return templates.TemplateResponse("login.html", {
            "request": request,
            "t": t,
            "rtl": is_rtl(lang),
            "lang": lang,
            "error": t.get("login_wrong", "Incorrect email or password"),
            "not_verified": "",
            "resent": "",
        }, status_code=400)

    if not user.email_verified and is_smtp_configured():
        encoded_email = quote(user.email, safe="")
        return RedirectResponse(
            f"/login?not_verified=1&email={encoded_email}",
            status_code=302,
        )

    token = create_access_token(user.id)
    lang = user.language or "en"
    redirect = RedirectResponse("/swipe", status_code=302)
    _set_auth_cookie(redirect, token)
    redirect.set_cookie("lang", lang, max_age=60 * 60 * 24 * 365, samesite="lax")
    return redirect


@router.get("/register", response_class=HTMLResponse)
def register_page(request: Request, user=Depends(get_optional_user)):
    if user:
        return RedirectResponse("/swipe", status_code=302)
    lang = get_lang(request)
    ref = request.query_params.get("ref", "")
    return templates.TemplateResponse("register.html", {
        "request": request,
        "t": get_translations(lang),
        "rtl": is_rtl(lang),
        "ref": ref,
    })


@router.post("/register", dependencies=[Depends(rate_limit(5, 60)), Depends(validate_csrf_form)])
def register(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    language: str = Form("en"),
    ref: str = Form(""),
    db: Session = Depends(get_db),
):
    email = email.lower().strip()

    from app.i18n import VALID_LANGS
    allowed_languages = VALID_LANGS
    if language not in allowed_languages:
        language = "en"

    t = get_translations(language)

    if db.query(User).filter(User.email == email).first():
        return templates.TemplateResponse("register.html", {
            "request": request,
            "t": t,
            "rtl": is_rtl(language),
            "lang": language,
            "ref": ref,
            "error": t.get("register_email_taken", "Email already registered"),
        }, status_code=400)

    if len(password) < 8:
        return templates.TemplateResponse("register.html", {
            "request": request,
            "t": t,
            "rtl": is_rtl(language),
            "lang": language,
            "ref": ref,
            "error": t.get("register_password_short", "Password must be at least 8 characters"),
        }, status_code=400)

    smtp_active = is_smtp_configured()
    verify_token = secrets.token_urlsafe(32) if smtp_active else None

    # Look up referrer before creating new user
    ref_code = ref.strip().upper() if ref else ""
    referrer = None
    if ref_code:
        referrer = db.query(User).filter(User.referral_code == ref_code).first()

    from app.routers.referral import _generate_referral_code
    user = User(
        email=email,
        hashed_password=hash_password(password),
        language=language,
        email_verified=not smtp_active,
        email_verify_token=verify_token,
        # FIX H7: record when token was issued so we can enforce 24h expiry
        email_verify_created_at=_utcnow() if smtp_active else None,
        referred_by_id=referrer.id if referrer else None,
        referral_code=_generate_referral_code(db),
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    # Reward referrer with bonus premium days
    if referrer:
        from app.routers.referral import apply_referral_bonus
        apply_referral_bonus(referrer, db)

    if smtp_active:
        send_verification_email(email, verify_token, lang=language)
        return RedirectResponse("/register/check-email", status_code=302)

    token = create_access_token(user.id)
    redirect = RedirectResponse("/profile/edit", status_code=302)
    _set_auth_cookie(redirect, token)
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
def verify_email(token: str, request: Request, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email_verify_token == token).first()
    if not user:
        lang = get_lang(request)
        t = get_translations(lang)
        return templates.TemplateResponse("verify_error.html", {
            "request": request,
            "t": t,
            "rtl": is_rtl(lang),
            "lang": lang,
            "error_key": "verify_invalid",
            "error_msg": t.get("verify_invalid_link", "Ссылка недействительна или уже использована."),
        }, status_code=400)

    # FIX H7: enforce 24-hour token expiry
    if user.email_verify_created_at:
        age_seconds = (_utcnow() - user.email_verify_created_at).total_seconds()
        if age_seconds > _EMAIL_VERIFY_TTL_SECONDS:
            lang = user.language or get_lang(request)
            t = get_translations(lang)
            return templates.TemplateResponse("verify_error.html", {
                "request": request,
                "t": t,
                "rtl": is_rtl(lang),
                "lang": lang,
                "error_key": "verify_expired",
                "error_msg": t.get("verify_expired_link", "Ссылка истекла (действует 24 ч). Войдите, чтобы получить новую."),
                "show_resend": True,
                "resend_email": user.email,
            }, status_code=400)

    user.email_verified = True
    user.email_verify_token = None
    user.email_verify_created_at = None
    db.commit()

    access_token = create_access_token(user.id)
    lang = user.language or "en"
    redirect = RedirectResponse("/profile/edit", status_code=302)
    _set_auth_cookie(redirect, access_token)  # C3: use helper so secure= flag is applied
    redirect.set_cookie("lang", lang, max_age=60 * 60 * 24 * 365, samesite="lax")
    return redirect


@router.post("/resend-verification", dependencies=[Depends(rate_limit(3, 300)), Depends(validate_csrf_form)])
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
        # FIX H7: reset the expiry clock on resend
        user.email_verify_created_at = _utcnow()
        db.commit()
        send_verification_email(email, new_token, lang=user.language or "en")

    # Always redirect to avoid email enumeration
    encoded_email = quote(email, safe="")
    return RedirectResponse(f"/login?not_verified=1&email={encoded_email}&resent=1", status_code=302)


@router.post("/logout", dependencies=[Depends(validate_csrf_form)])
def logout():
    redirect = RedirectResponse("/login", status_code=302)
    redirect.delete_cookie("access_token")
    redirect.delete_cookie("lang")
    return redirect
