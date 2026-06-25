import os
import re
import secrets
from urllib.parse import quote

_EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,255}\.[^@\s]{1,63}$")

from fastapi import APIRouter, BackgroundTasks, Depends, Form, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import (
    DUMMY_HASH,
    create_access_token,
    hash_password,
    verify_password,
    get_optional_user,
)
from app.csrf import validate_csrf_form
from app.database import get_db
from app.email_utils import is_smtp_configured, send_verification_email, send_password_reset_email
from app.i18n import get_lang, get_translations, is_rtl
from app.models.models import User
from app.rate_limit import rate_limit
from app.templates import templates

router = APIRouter()

_EMAIL_VERIFY_TTL_SECONDS = 86400  # 24 hours
_SECURE_COOKIES = bool(os.getenv("RAILWAY_ENVIRONMENT") or os.getenv("SECURE_COOKIES"))

from app.utils.time import utcnow as _utcnow


def _set_auth_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        "access_token", token,
        httponly=True,
        max_age=60 * 60 * 24 * 7,
        samesite="lax",
        secure=_SECURE_COOKIES,
    )


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, user=Depends(get_optional_user)):
    if user:
        return RedirectResponse("/swipe", status_code=302)
    lang = get_lang(request)
    return templates.TemplateResponse(request, "login.html", {
        "t": get_translations(lang),
        "rtl": is_rtl(lang),
        "not_verified": request.query_params.get("not_verified", ""),
        "resent": request.query_params.get("resent", ""),
    })


_MAX_FAILED_LOGINS = 5
_LOCKOUT_MINUTES = 15


@router.post("/login", dependencies=[Depends(rate_limit(10, 60)), Depends(validate_csrf_form)])
async def login(
    request: Request,
    response: Response,
    email: str = Form(...),
    password: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    from datetime import timedelta

    result = await db.execute(select(User).where(User.email == email.lower().strip()))
    user = result.scalar_one_or_none()

    lang = (user.language if user and user.language else None) or get_lang(request)
    t = get_translations(lang)

    # Check account lockout
    if user and user.locked_until and user.locked_until > _utcnow():
        remaining = int((user.locked_until - _utcnow()).total_seconds() // 60) + 1
        error_msg = t.get("account_locked", f"Аккаунт заблокирован. Попробуйте через {remaining} мин.")
        return templates.TemplateResponse(request, "login.html", {
            "t": t, "rtl": is_rtl(lang), "lang": lang,
            "error": error_msg, "not_verified": "", "resent": "",
        }, status_code=429)

    password_ok = verify_password(password, user.hashed_password if user else DUMMY_HASH)

    if not user or not password_ok:
        if user:
            user.failed_logins = (user.failed_logins or 0) + 1
            if user.failed_logins >= _MAX_FAILED_LOGINS:
                user.locked_until = _utcnow() + timedelta(minutes=_LOCKOUT_MINUTES)
            await db.commit()
        return templates.TemplateResponse(request, "login.html", {
            "t": t, "rtl": is_rtl(lang), "lang": lang,
            "error": t.get("login_wrong", "Incorrect email or password"),
            "not_verified": "", "resent": "",
        }, status_code=400)

    # Success — reset lockout counters
    if user.failed_logins or user.locked_until:
        user.failed_logins = 0
        user.locked_until = None
        await db.commit()

    token = create_access_token(user.id, token_version=user.token_version or 0)
    lang = user.language or "en"
    redirect = RedirectResponse("/swipe", status_code=302)
    _set_auth_cookie(redirect, token)
    redirect.set_cookie("lang", lang, max_age=60 * 60 * 24 * 365, samesite="lax")
    return redirect


@router.get("/register", response_class=HTMLResponse)
async def register_page(request: Request, user=Depends(get_optional_user)):
    if user:
        return RedirectResponse("/swipe", status_code=302)
    lang = get_lang(request)
    ref = request.query_params.get("ref", "")
    return templates.TemplateResponse(request, "register.html", {
        "t": get_translations(lang),
        "rtl": is_rtl(lang),
        "ref": ref,
    })


@router.post("/register", dependencies=[Depends(rate_limit(5, 60)), Depends(validate_csrf_form)])
async def register(
    request: Request,
    background_tasks: BackgroundTasks,
    email: str = Form(...),
    password: str = Form(...),
    language: str = Form("en"),
    ref: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    email = email.lower().strip()

    from app.i18n import VALID_LANGS
    allowed_languages = VALID_LANGS
    if language not in allowed_languages:
        language = "en"

    t = get_translations(language)

    if len(email) > 254 or not _EMAIL_RE.match(email):
        return templates.TemplateResponse(request, "register.html", { "t": t, "rtl": is_rtl(language),
            "lang": language, "ref": ref,
            "error": t.get("register_email_invalid", "Invalid email address"),
        }, status_code=400)

    result = await db.execute(select(User).where(User.email == email))
    if result.scalar_one_or_none():
        # Don't reveal whether this email is registered — redirect to check-email page
        return RedirectResponse("/login?check_email=1", status_code=302)

    if len(password) < 8:
        return templates.TemplateResponse(request, "register.html", {
            "t": t, "rtl": is_rtl(language), "lang": language, "ref": ref,
            "error": t.get("register_password_short", "Password must be at least 8 characters"),
        }, status_code=400)

    if not any(c.isdigit() for c in password):
        return templates.TemplateResponse(request, "register.html", {
            "t": t, "rtl": is_rtl(language), "lang": language, "ref": ref,
            "error": t.get("register_password_digit", "Password must contain at least one digit"),
        }, status_code=400)

    ref_code = ref.strip().upper() if ref else ""
    referrer = None
    if ref_code:
        result = await db.execute(select(User).where(User.referral_code == ref_code))
        referrer = result.scalar_one_or_none()

    from app.routers.referral import _generate_referral_code
    user = User(
        email=email,
        hashed_password=hash_password(password),
        language=language,
        email_verified=True,
        referred_by_id=referrer.id if referrer else None,
        referral_code=await _generate_referral_code(db),
    )
    from sqlalchemy.exc import IntegrityError as _IE
    db.add(user)
    try:
        await db.commit()
    except _IE:
        await db.rollback()
        return templates.TemplateResponse(request, "register.html", { "t": t, "rtl": is_rtl(language),
            "lang": language, "ref": ref,
            "error": t.get("register_email_taken", "Email already registered"),
        }, status_code=400)
    await db.refresh(user)

    if referrer:
        from app.routers.referral import apply_referral_bonus
        await apply_referral_bonus(referrer, db)

    token = create_access_token(user.id)
    redirect = RedirectResponse("/welcome", status_code=302)
    _set_auth_cookie(redirect, token)
    redirect.set_cookie("lang", language, max_age=60 * 60 * 24 * 365, samesite="lax")
    return redirect


@router.get("/register/check-email", response_class=HTMLResponse)
def check_email_page(request: Request):
    lang = get_lang(request)
    return templates.TemplateResponse(request, "check_email.html", {
        "t": get_translations(lang),
        "rtl": is_rtl(lang),
    })


@router.get("/verify-email/{token}", response_class=HTMLResponse, dependencies=[Depends(rate_limit(10, 60))])
async def verify_email(token: str, request: Request, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.email_verify_token == token))
    user = result.scalar_one_or_none()
    if not user:
        lang = get_lang(request)
        t = get_translations(lang)
        return templates.TemplateResponse(request, "verify_error.html", {
            "t": t, "rtl": is_rtl(lang), "lang": lang,
            "error_key": "verify_invalid",
            "error_msg": t.get("verify_invalid_link", "Ссылка недействительна или уже использована."),
        }, status_code=400)

    if user.email_verify_created_at:
        age_seconds = (_utcnow() - user.email_verify_created_at).total_seconds()
        if age_seconds > _EMAIL_VERIFY_TTL_SECONDS:
            lang = user.language or get_lang(request)
            t = get_translations(lang)
            return templates.TemplateResponse(request, "verify_error.html", {
                "t": t, "rtl": is_rtl(lang), "lang": lang,
                "error_key": "verify_expired",
                "error_msg": t.get("verify_expired_link", "Ссылка истекла (действует 24 ч). Войдите, чтобы получить новую."),
                "show_resend": True,
                "resend_email": user.email,
            }, status_code=400)

    user.email_verified = True
    user.email_verify_token = None
    user.email_verify_created_at = None
    await db.commit()

    access_token = create_access_token(user.id)
    lang = user.language or "en"
    redirect = RedirectResponse("/welcome", status_code=302)
    _set_auth_cookie(redirect, access_token)
    redirect.set_cookie("lang", lang, max_age=60 * 60 * 24 * 365, samesite="lax")
    return redirect


@router.post("/resend-verification", dependencies=[Depends(rate_limit(3, 300)), Depends(validate_csrf_form)])
async def resend_verification(
    request: Request,
    background_tasks: BackgroundTasks,
    email: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    email = email.lower().strip()
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()

    if user and not user.email_verified:
        new_token = secrets.token_urlsafe(32)
        user.email_verify_token = new_token
        user.email_verify_created_at = _utcnow()
        await db.commit()
        background_tasks.add_task(send_verification_email, email, new_token, lang=user.language or "en")

    return RedirectResponse("/login?not_verified=1&resent=1", status_code=302)


@router.post("/logout", dependencies=[Depends(validate_csrf_form)])
def logout():
    redirect = RedirectResponse("/login", status_code=302)
    # Must match the same attributes used in _set_auth_cookie, otherwise browsers ignore deletion
    redirect.delete_cookie("access_token", httponly=True, samesite="lax", secure=_SECURE_COOKIES)
    redirect.delete_cookie("lang", samesite="lax")
    return redirect


# ─── Password reset ───────────────────────────────────────────────────────────

@router.get("/forgot-password", response_class=HTMLResponse)
async def forgot_password_page(request: Request, user=Depends(get_optional_user)):
    if user:
        return RedirectResponse("/swipe", status_code=302)
    lang = get_lang(request)
    return templates.TemplateResponse(request, "forgot_password.html", {
        "t": get_translations(lang),
        "rtl": is_rtl(lang),
    })


@router.post("/forgot-password", dependencies=[Depends(rate_limit(3, 300)), Depends(validate_csrf_form)])
async def forgot_password(
    request: Request,
    background_tasks: BackgroundTasks,
    email: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    email = email.lower().strip()
    lang = get_lang(request)
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()

    if user:
        token = secrets.token_urlsafe(32)
        from datetime import timedelta
        user.password_reset_token = token
        user.password_reset_expires = _utcnow() + timedelta(hours=1)
        await db.commit()
        background_tasks.add_task(send_password_reset_email, email, token, lang=user.language or lang)

    return RedirectResponse("/forgot-password?sent=1", status_code=302)


@router.get("/reset-password/{token}", response_class=HTMLResponse, dependencies=[Depends(rate_limit(10, 60))])
async def reset_password_page(token: str, request: Request, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.password_reset_token == token))
    user = result.scalar_one_or_none()
    lang = (user.language if user else None) or get_lang(request)
    t = get_translations(lang)

    if not user or not user.password_reset_expires or _utcnow() > user.password_reset_expires:
        return templates.TemplateResponse(request, "reset_password.html", {
            "t": t, "rtl": is_rtl(lang), "token": token, "invalid": True,
        }, status_code=400)

    return templates.TemplateResponse(request, "reset_password.html", {
        "t": t, "rtl": is_rtl(lang), "token": token, "invalid": False,
    })


@router.post("/reset-password/{token}", dependencies=[Depends(rate_limit(5, 60)), Depends(validate_csrf_form)])
async def reset_password(
    token: str,
    request: Request,
    response: Response,
    password: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(User).where(User.password_reset_token == token))
    user = result.scalar_one_or_none()
    lang = (user.language if user else None) or get_lang(request)
    t = get_translations(lang)

    if not user or not user.password_reset_expires or _utcnow() > user.password_reset_expires:
        return templates.TemplateResponse(request, "reset_password.html", {
            "t": t, "rtl": is_rtl(lang), "token": token, "invalid": True,
        }, status_code=400)

    if len(password) < 8 or not any(c.isdigit() for c in password):
        return templates.TemplateResponse(request, "reset_password.html", {
            "t": t, "rtl": is_rtl(lang), "token": token, "invalid": False,
            "error": t.get("register_password_short", "Минимум 8 символов, включая цифру"),
        }, status_code=400)

    user.hashed_password = hash_password(password)
    user.password_reset_token = None
    user.password_reset_expires = None
    user.token_version = (user.token_version or 0) + 1
    await db.commit()

    access_token = create_access_token(user.id, token_version=user.token_version)
    redirect = RedirectResponse("/swipe", status_code=302)
    _set_auth_cookie(redirect, access_token)
    redirect.set_cookie("lang", user.language or "en", max_age=60 * 60 * 24 * 365, samesite="lax")
    return redirect
