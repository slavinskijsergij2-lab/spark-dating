import json
import logging
import os
import secrets
import traceback
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text as _text

logging.basicConfig(level=logging.INFO)

from app.database import Base, engine
from app.i18n import get_lang, get_translations, is_rtl
from app.routers import auth, profile, swipe, matches
from app.utils.time import utcnow as _utcnow
from app.routers import features, premium, social, stories, referral, admin_seed
from app.templates import templates

Base.metadata.create_all(bind=engine)

# Inline DB migrations — safe to run on every startup (IF NOT EXISTS / try-except)
_is_pg = str(engine.url).startswith("postgresql")
_migrations = [
    "ALTER TABLE users ADD COLUMN{} last_seen TIMESTAMP",
    "ALTER TABLE users ADD COLUMN{} email_verified BOOLEAN NOT NULL DEFAULT TRUE",
    "ALTER TABLE users ADD COLUMN{} email_verify_token VARCHAR(100)",
    "ALTER TABLE users ADD COLUMN{} email_verify_created_at TIMESTAMP",
    "ALTER TABLE users ADD COLUMN{} is_premium BOOLEAN NOT NULL DEFAULT FALSE",
    "ALTER TABLE users ADD COLUMN{} boost_until TIMESTAMP",
    "ALTER TABLE users ADD COLUMN{} birth_date TIMESTAMP",
    "ALTER TABLE users ADD COLUMN{} phone VARCHAR(20)",
    "ALTER TABLE users ADD COLUMN{} phone_verified BOOLEAN NOT NULL DEFAULT FALSE",
    "ALTER TABLE profiles ADD COLUMN{} interests VARCHAR(500)",
    "ALTER TABLE profiles ADD COLUMN{} is_anonymous BOOLEAN NOT NULL DEFAULT FALSE",
    "ALTER TABLE likes ADD COLUMN{} is_super BOOLEAN NOT NULL DEFAULT FALSE",
    "ALTER TABLE matches ADD COLUMN{} seen_by_user1 BOOLEAN NOT NULL DEFAULT TRUE",
    "ALTER TABLE matches ADD COLUMN{} seen_by_user2 BOOLEAN NOT NULL DEFAULT FALSE",
    "ALTER TABLE matches ADD COLUMN{} streak_days INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE matches ADD COLUMN{} last_streak_date TIMESTAMP",
    "ALTER TABLE matches ADD COLUMN{} user1_revealed BOOLEAN NOT NULL DEFAULT TRUE",
    "ALTER TABLE matches ADD COLUMN{} user2_revealed BOOLEAN NOT NULL DEFAULT TRUE",
    "ALTER TABLE messages ADD COLUMN{} is_voice BOOLEAN NOT NULL DEFAULT FALSE",
    # HIGH-8: both parties should see new match notification
    "ALTER TABLE matches ALTER COLUMN seen_by_user1 SET DEFAULT FALSE",
    # Referral system
    "ALTER TABLE users ADD COLUMN{} referral_code VARCHAR(20)",
    "ALTER TABLE users ADD COLUMN{} referred_by_id INTEGER",
    "ALTER TABLE users ADD COLUMN{} premium_until TIMESTAMP",
]
# MEDIUM-5: add unique constraint on profile_views to prevent duplicates
_constraint_migrations = [
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_profile_view ON profile_views (viewer_id, viewed_id)",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_referral_code ON users (referral_code) WHERE referral_code IS NOT NULL",
]
_type_migrations = [
    "ALTER TABLE profiles ALTER COLUMN photo TYPE TEXT",
    "ALTER TABLE profile_photos ALTER COLUMN url TYPE TEXT",
]
for _m in _migrations:
    _sql = _m.format(" IF NOT EXISTS" if _is_pg else "")
    try:
        with engine.begin() as _c:
            _c.execute(_text(_sql))
    except Exception:
        pass

if _is_pg:
    for _sql in _type_migrations:
        try:
            with engine.begin() as _c:
                _c.execute(_text(_sql))
        except Exception:
            pass

for _sql in _constraint_migrations:
    try:
        with engine.begin() as _c:
            _c.execute(_text(_sql))
    except Exception:
        pass

app = FastAPI(title="Spark — сайт знакомств")
app.mount("/static", StaticFiles(directory="static"), name="static")

# C3: If PHOTO_DIR is set (e.g. Railway Volume /data/photos), serve photos from there.
# Without it, photos go to ./static/photos/ which is already covered by the /static mount.
_PHOTO_ENV_DIR = os.getenv("PHOTO_DIR", "")
if _PHOTO_ENV_DIR:
    from pathlib import Path as _Path
    _Path(_PHOTO_ENV_DIR).mkdir(parents=True, exist_ok=True)
    app.mount("/photos", StaticFiles(directory=_PHOTO_ENV_DIR), name="photos")

# HIGH-6: Reject oversized request bodies before they reach route handlers.
# Prevents DoS via 100 MB audio/image uploads buffered into memory.
_MAX_BODY_BYTES = 12 * 1024 * 1024  # 12 MB ceiling

@app.middleware("http")
async def max_body_size_middleware(request: Request, call_next):
    content_length = request.headers.get("content-length")
    if content_length and int(content_length) > _MAX_BODY_BYTES:
        return JSONResponse({"detail": "Request body too large (max 12 MB)"}, status_code=413)
    return await call_next(request)

_CSRF_COOKIE = "csrftoken"


@app.middleware("http")
async def security_middleware(request: Request, call_next):
    # Double-submit CSRF cookie: ensure every request has a token in state
    csrf_token = request.cookies.get(_CSRF_COOKIE) or secrets.token_urlsafe(32)
    request.state.csrf_token = csrf_token

    response = await call_next(request)

    # Set cookie on first visit (httponly=False — JS needs to read it for AJAX)
    if not request.cookies.get(_CSRF_COOKIE):
        response.set_cookie(
            _CSRF_COOKIE, csrf_token,
            httponly=False, samesite="lax", max_age=60 * 60 * 24 * 7,
        )

    # Security headers
    csp = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://cdn.tailwindcss.com https://cdn.jsdelivr.net; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: blob:; "
        "connect-src 'self'; "
        "frame-ancestors 'none';"
    )
    response.headers.setdefault("Content-Security-Policy", csp)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")

    return response


def _tojson(value, indent=None):
    from datetime import datetime as _dt
    def default(o):
        if isinstance(o, _dt):
            return o.isoformat()
        raise TypeError(f"Object of type {type(o)} is not JSON serializable")
    result = json.dumps(value, default=default, indent=indent, ensure_ascii=False)
    # Escape </script> and HTML comment sequences so injected content can't break
    # a <script> block. Return plain str so Jinja2 autoescape encodes " → &quot;
    # in HTML attributes (x-data, x-init). Use | safe in <script> contexts.
    return result.replace("</", "<\\/").replace("<!--", "<\\!--")


_ONLINE_LABELS = {
    "ru": ("Онлайн", "{n} мин назад", "{n} ч назад"),
    "uk": ("Онлайн", "{n} хв тому", "{n} год тому"),
    "en": ("Online", "{n}m ago", "{n}h ago"),
    "de": ("Online", "vor {n}m", "vor {n}h"),
    "tr": ("Çevrimiçi", "{n}d önce", "{n}s önce"),
    "ar": ("متصل", "منذ {n}د", "منذ {n}س"),
}


def _online_status(last_seen, lang="en"):
    if not last_seen:
        return None
    diff = (_utcnow() - last_seen).total_seconds()
    online_lbl, mins_lbl, hrs_lbl = _ONLINE_LABELS.get(lang, _ONLINE_LABELS["en"])
    if diff < 300:
        return {"is_online": True, "label": online_lbl}
    if diff < 3600:
        return {"is_online": False, "label": mins_lbl.replace("{n}", str(int(diff / 60)))}
    if diff < 86400:
        return {"is_online": False, "label": hrs_lbl.replace("{n}", str(int(diff / 3600)))}
    return None


templates.env.filters["tojson"] = _tojson
templates.env.globals["online_status"] = _online_status
templates.env.globals["now"] = _utcnow

app.include_router(auth.router)
app.include_router(profile.router)
app.include_router(swipe.router)
app.include_router(matches.router)
app.include_router(features.router)
app.include_router(premium.router)
app.include_router(social.router)
app.include_router(stories.router)
app.include_router(referral.router)
app.include_router(admin_seed.router)


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    from urllib.parse import quote
    accept = request.headers.get("accept", "")
    is_api = request.url.path.startswith("/api/")
    if exc.status_code == 401 and not is_api:
        # Always redirect browser page requests to login and clear stale cookie
        response = RedirectResponse("/login", status_code=302)
        response.delete_cookie("access_token")
        return response
    if "text/html" in accept:
        if exc.status_code == 401:
            response = RedirectResponse("/login", status_code=302)
            response.delete_cookie("access_token")
            return response
        if exc.status_code in (400, 403, 422):
            path = request.url.path or "/"
            msg = quote(str(exc.detail), safe="")
            return RedirectResponse(f"{path}?error={msg}", status_code=302)
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    accept = request.headers.get("accept", "")
    if "text/html" in accept:
        path = request.url.path
        return RedirectResponse(f"{path}?error=validation", status_code=302)
    return JSONResponse(status_code=422, content={"detail": exc.errors()})


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    tb = traceback.format_exc()
    logging.error("Unhandled exception on %s %s:\n%s", request.method, request.url.path, tb)
    # FIX H8: return HTML error page to browser users, not raw JSON
    accept = request.headers.get("accept", "")
    if "text/html" in accept:
        try:
            lang = get_lang(request)
            t = get_translations(lang)
            err_title = t.get("error_500_title", "Something went wrong 😔")
            err_body  = t.get("error_500_body", "Please refresh the page or try again later.")
            err_home  = t.get("error_500_home", "Go home")
        except Exception:
            err_title, err_body, err_home = "Something went wrong 😔", "Please try again later.", "Home"
        return HTMLResponse(
            f"<html><body style='font-family:sans-serif;text-align:center;padding:80px 20px;'>"
            f"<h2 style='color:#ef4444;'>{err_title}</h2>"
            f"<p style='color:#6b7280;'>{err_body}</p>"
            f"<a href='/' style='color:#ec4899;font-weight:600;'>{err_home}</a>"
            f"</body></html>",
            status_code=500,
        )
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return FileResponse("static/favicon.ico", media_type="image/x-icon")


@app.get("/health")
def health():
    # FIX H6: do not expose DB host/credentials in response
    try:
        with engine.connect() as conn:
            conn.execute(_text("SELECT 1"))
        return {"status": "ok"}
    except Exception as e:
        logging.error("Health check failed: %s", e)
        return JSONResponse(status_code=500, content={"status": "error"})


@app.get("/", response_class=HTMLResponse)
@app.head("/")
def index(request: Request):
    token = request.cookies.get("access_token")
    if token:
        return RedirectResponse("/swipe", status_code=302)
    lang = get_lang(request)
    return templates.TemplateResponse("index.html", {
        "request": request,
        "t": get_translations(lang),
        "rtl": is_rtl(lang),
        "lang": lang,
    })
