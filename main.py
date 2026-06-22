import html as _html
import json
import logging
import os
import secrets
import time
import traceback
from collections import defaultdict
from pathlib import Path
from threading import Lock

from dotenv import load_dotenv
load_dotenv()

from app.logging_config import setup_logging
setup_logging()

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text as _text

from app.database import Base, engine
from app.i18n import get_lang, get_translations, is_rtl
from app.routers import auth, profile, swipe, matches
from app.utils.time import utcnow as _utcnow
from app.routers import features, premium, social, stories, referral
from app.templates import templates


def _run_alembic_migrations() -> None:
    from alembic.config import Config
    from alembic import command
    from alembic.runtime.migration import MigrationContext

    alembic_cfg = Config("alembic.ini")

    with engine.connect() as conn:
        current = MigrationContext.configure(conn).get_current_revision()

    if current is None:
        try:
            # Detect pre-Alembic deployment: schema already exists
            with engine.connect() as conn:
                conn.execute(_text("SELECT 1 FROM users LIMIT 1"))
            # Stamp at 001 (not head) so any newer migrations still run below
            command.stamp(alembic_cfg, "001")
            logging.info("alembic: stamped existing database as 001")
        except Exception:
            # Fresh database — create schema via migrations
            command.upgrade(alembic_cfg, "head")
            logging.info("alembic: created schema via migrations")
    else:
        command.upgrade(alembic_cfg, "head")
        if current != "head":
            logging.info("alembic: applied pending migrations (was at %s)", current)


try:
    _run_alembic_migrations()
except Exception as _alembic_err:
    logging.error("alembic: migration failed — %s", _alembic_err)
    raise

app = FastAPI(title="Spark — сайт знакомств")
app.mount("/static", StaticFiles(directory="static"), name="static")

# ── Simple in-process metrics ─────────────────────────────────────────────────
_m_lock = Lock()
_m: dict = {
    "started_at": time.time(),
    "requests_total": 0,
    "status_counts": defaultdict(int),
    "errors_5xx": 0,
}

_SKIP_LOG = ("/static/", "/photos/", "/health", "/favicon", "/metrics")

@app.middleware("http")
async def request_logging_middleware(request: Request, call_next):
    t0 = time.perf_counter()
    response = await call_next(request)
    ms = round((time.perf_counter() - t0) * 1000)
    status = response.status_code
    path = request.url.path
    with _m_lock:
        _m["requests_total"] += 1
        _m["status_counts"][str(status)] += 1
        if status >= 500:
            _m["errors_5xx"] += 1
    if not any(path.startswith(p) for p in _SKIP_LOG):
        logging.info("http", extra={
            "method": request.method,
            "path": path,
            "status": status,
            "ms": ms,
        })
    return response
# ─────────────────────────────────────────────────────────────────────────────

# /photos is always mounted.
# On Railway with a Volume: set PHOTO_DIR=/data/photos — files survive redeploys.
# In local dev (or Railway without a Volume): falls back to static/photos/ inside the container.
_PHOTO_DIR = os.getenv("PHOTO_DIR", "static/photos")
Path(_PHOTO_DIR).mkdir(parents=True, exist_ok=True)
app.mount("/photos", StaticFiles(directory=_PHOTO_DIR), name="photos")


def _migrate_base64_photos() -> None:
    """
    One-time startup migration: converts base64 data URIs stored in PostgreSQL
    to real files on disk.  Safe to call on every startup — bails out immediately
    if no base64 rows remain.
    """
    import base64 as _b64
    import uuid as _uuid
    from sqlalchemy import text as _t

    photo_dir = Path(_PHOTO_DIR)
    photo_dir.mkdir(parents=True, exist_ok=True)

    def _flush(b64_str: str) -> str:
        if not b64_str or not b64_str.startswith("data:image/"):
            return b64_str
        try:
            _hdr, data = b64_str.split(",", 1)
            raw = _b64.b64decode(data)
            fname = f"{_uuid.uuid4().hex}.jpg"
            (photo_dir / fname).write_bytes(raw)
            return f"/photos/{fname}"
        except Exception as exc:
            logging.warning("migrate_photos: could not decode entry: %s", exc)
            return b64_str

    with engine.begin() as conn:
        # Fast check — skip migration entirely if nothing to do
        n_profiles = conn.execute(_t(
            "SELECT COUNT(*) FROM profiles WHERE photo LIKE 'data:image/%'"
        )).scalar() or 0
        n_gallery = conn.execute(_t(
            "SELECT COUNT(*) FROM profile_photos WHERE url LIKE 'data:image/%'"
        )).scalar() or 0
        n_stories = conn.execute(_t(
            "SELECT COUNT(*) FROM stories WHERE media_type='image' AND content LIKE 'data:image/%'"
        )).scalar() or 0

        total = n_profiles + n_gallery + n_stories
        if total == 0:
            return

        logging.info("migrate_photos: found %d base64 rows — converting to files …", total)

        # Profiles
        if n_profiles:
            rows = conn.execute(_t(
                "SELECT id, photo FROM profiles WHERE photo LIKE 'data:image/%'"
            )).fetchall()
            for row_id, photo in rows:
                new_url = _flush(photo)
                conn.execute(_t("UPDATE profiles SET photo=:u WHERE id=:id"),
                             {"u": new_url, "id": row_id})

        # Gallery
        if n_gallery:
            rows = conn.execute(_t(
                "SELECT id, url FROM profile_photos WHERE url LIKE 'data:image/%'"
            )).fetchall()
            for row_id, url in rows:
                new_url = _flush(url)
                conn.execute(_t("UPDATE profile_photos SET url=:u WHERE id=:id"),
                             {"u": new_url, "id": row_id})

        # Stories
        if n_stories:
            rows = conn.execute(_t(
                "SELECT id, content FROM stories WHERE media_type='image' AND content LIKE 'data:image/%'"
            )).fetchall()
            for row_id, content in rows:
                new_url = _flush(content)
                conn.execute(_t("UPDATE stories SET content=:u WHERE id=:id"),
                             {"u": new_url, "id": row_id})

    logging.info("migrate_photos: done — %d records converted to /photos/", total)


try:
    _migrate_base64_photos()
except Exception as _mig_err:
    logging.error("migrate_photos: startup migration failed: %s", _mig_err)

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
    response.headers.setdefault("Permissions-Policy", "geolocation=(), microphone=(), camera=()")
    response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    # HSTS only in production (Railway sets RAILWAY_ENVIRONMENT)
    if os.getenv("RAILWAY_ENVIRONMENT"):
        response.headers.setdefault(
            "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
        )

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
app.include_router(premium.router)   # before profile.router: /profile/who-viewed must not be caught by /profile/{user_id}
app.include_router(profile.router)
app.include_router(swipe.router)
app.include_router(matches.router)
app.include_router(features.router)
app.include_router(social.router)
app.include_router(stories.router)
app.include_router(referral.router)


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
            f"<h2 style='color:#ef4444;'>{_html.escape(err_title)}</h2>"
            f"<p style='color:#6b7280;'>{_html.escape(err_body)}</p>"
            f"<a href='/' style='color:#ec4899;font-weight:600;'>{_html.escape(err_home)}</a>"
            f"</body></html>",
            status_code=500,
        )
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return FileResponse("static/favicon.ico", media_type="image/x-icon")


@app.get("/health")
def health():
    try:
        with engine.connect() as conn:
            conn.execute(_text("SELECT 1"))
        return {"status": "ok"}
    except Exception as e:
        logging.error("Health check failed: %s", e)
        return JSONResponse(status_code=500, content={"status": "error"})


@app.get("/metrics")
def app_metrics(token: str = Query(default="")):
    required = os.getenv("METRICS_TOKEN", "")
    if required and token != required:
        raise HTTPException(403, "Forbidden")
    with _m_lock:
        return JSONResponse({
            "uptime_seconds": int(time.time() - _m["started_at"]),
            "requests_total": _m["requests_total"],
            "errors_5xx": _m["errors_5xx"],
            "status_counts": dict(_m["status_counts"]),
        })


@app.get("/", response_class=HTMLResponse)
@app.head("/")
def index(request: Request):
    token = request.cookies.get("access_token")
    if token:
        return RedirectResponse("/swipe", status_code=302)
    lang = get_lang(request)
    return templates.TemplateResponse(request, "index.html", {
        "t": get_translations(lang),
        "rtl": is_rtl(lang),
        "lang": lang,
    })
