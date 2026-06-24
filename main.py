import asyncio
import html as _html
import json
import logging
import os
import secrets
import time
import traceback
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path
from threading import Lock

from dotenv import load_dotenv
load_dotenv()

from app.logging_config import setup_logging
setup_logging()

# Sentry error tracking — enabled when SENTRY_DSN env var is set on Railway.
_sentry_dsn = os.getenv("SENTRY_DSN")
if _sentry_dsn:
    import sentry_sdk
    sentry_sdk.init(
        dsn=_sentry_dsn,
        traces_sample_rate=0.05,
        environment=os.getenv("RAILWAY_ENVIRONMENT", "development"),
        send_default_pii=False,
    )
    logging.info("startup: Sentry enabled (environment=%s)", os.getenv("RAILWAY_ENVIRONMENT", "development"))

logging.info("startup: imports begin")

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text as _text

logging.info("startup: fastapi+sqlalchemy imported")

from app.database import Base, engine
from app.i18n import get_lang, get_translations, is_rtl
from app.routers import auth, profile, swipe, matches
from app.utils.time import utcnow as _utcnow
from app.routers import features, premium, social, stories, referral, push as push_router, admin as admin_router
from app.templates import templates

logging.info("startup: all app modules imported")


def _run_alembic_migrations() -> None:
    import time
    from alembic.config import Config
    from alembic import command
    from alembic.runtime.migration import MigrationContext

    alembic_cfg = Config("alembic.ini")

    # Retry DB connection: Railway PostgreSQL sometimes isn't ready when the app starts.
    for _attempt in range(5):
        try:
            with engine.connect() as conn:
                current = MigrationContext.configure(conn).get_current_revision()
            break
        except Exception as _e:
            if _attempt == 4:
                raise
            wait = 2 ** _attempt
            logging.warning("alembic: DB not ready (attempt %d/5), retrying in %ds: %s", _attempt + 1, wait, _e)
            time.sleep(wait)

    if current is None:
        try:
            # Detect pre-Alembic deployment: schema already exists
            with engine.connect() as conn:
                conn.execute(_text("SELECT 1 FROM users LIMIT 1"))
            command.stamp(alembic_cfg, "001")
            logging.info("alembic: stamped existing database as 001")
        except Exception:
            # Fresh database — run full migration from scratch
            command.upgrade(alembic_cfg, "head")
            logging.info("alembic: created schema via migrations")
            return

    # Always upgrade to head (runs pending migrations after stamp or on restart)
    command.upgrade(alembic_cfg, "head")


_startup_done: bool = bool(os.getenv("TESTING"))
_startup_ok: bool = bool(os.getenv("TESTING"))
_startup_time: float = time.time()


async def _run_startup_tasks() -> None:
    """Run all startup tasks in a background thread pool. Never raises."""
    global _startup_done, _startup_ok
    loop = asyncio.get_running_loop()
    logging.info("startup: running migrations")
    migrations_ok = False
    try:
        await loop.run_in_executor(None, _run_alembic_migrations)
        logging.info("startup: migrations OK")
        migrations_ok = True
    except Exception as _e:
        logging.error("startup: MIGRATION FAILED (app continues) — %s", _e, exc_info=True)
    try:
        await loop.run_in_executor(None, _fix_broken_photo_urls)
    except Exception as _e:
        logging.error("startup: fix_broken_photo_urls failed: %s", _e, exc_info=True)
    _startup_done = True
    _startup_ok = migrations_ok
    logging.info("startup: background startup tasks done (migrations_ok=%s)", migrations_ok)


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not os.getenv("TESTING"):
        asyncio.create_task(_run_startup_tasks())
        asyncio.create_task(_periodic_cleanup())
    yield


app = FastAPI(title="Spark — сайт знакомств", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")

# ── Simple in-process metrics ─────────────────────────────────────────────────
_m_lock = Lock()
_m: dict = {
    "started_at": time.time(),
    "requests_total": 0,
    "status_counts": defaultdict(int),
    "errors_5xx": 0,
}
_error_log: deque = deque(maxlen=50)  # last 50 unhandled errors
_error_email_cooldown: dict = {}  # exc_key -> last sent timestamp
_ERROR_EMAIL_COOLDOWN_S = 3600  # 1 email per unique error per hour


def _record_error(method: str, path: str, exc: Exception, tb: str) -> None:
    with _m_lock:
        _error_log.appendleft({
            "ts": _utcnow().isoformat(),
            "method": method,
            "path": path,
            "exc": f"{type(exc).__name__}: {exc}",
            "tb": tb[-2000:],
        })


async def _send_error_email(method: str, path: str, exc: Exception, tb: str) -> None:
    """Send error alert email via Resend. Throttled: 1 email per unique error per hour."""
    api_key = os.getenv("RESEND_API_KEY")
    if not api_key:
        return
    key = f"{type(exc).__name__}:{path}"
    now = time.time()
    with _m_lock:
        if now - _error_email_cooldown.get(key, 0) < _ERROR_EMAIL_COOLDOWN_S:
            return
        _error_email_cooldown[key] = now
    import httpx
    body_html = (
        f"<h3 style='color:#ef4444'>{_html.escape(type(exc).__name__)}: {_html.escape(str(exc))}</h3>"
        f"<p><b>{method}</b> {_html.escape(path)}</p>"
        f"<pre style='background:#1e1e1e;color:#d4d4d4;padding:16px;border-radius:8px;"
        f"font-size:12px;overflow:auto'>{_html.escape(tb[-3000:])}</pre>"
    )
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            await client.post(
                "https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={
                    "from": "Spark Errors <onboarding@resend.dev>",
                    "to": [os.getenv("ERROR_NOTIFY_EMAIL", "slavinskijsergij2@gmail.com")],
                    "subject": f"[Spark 🔥] {type(exc).__name__} on {path}",
                    "html": body_html,
                },
            )
    except Exception as _e:
        logging.warning("_send_error_email: failed to send: %s", _e)

_SKIP_LOG = ("/static/", "/photos/", "/health", "/favicon", "/metrics")

_STARTUP_PASSTHROUGH = frozenset(("/health", "/", "/favicon.ico"))
_STARTUP_PASSTHROUGH_PREFIXES = ("/static/", "/photos/")
_STARTUP_GRACE_SECONDS = 120  # block at most 2 min while migrations run


@app.middleware("http")
async def startup_readiness_middleware(request: Request, call_next):
    """Return 503 during the startup window so users see a clean retry instead
    of a 500 DB error. Disabled in TESTING mode and for health/static paths."""
    if not os.getenv("TESTING") and not _startup_done:
        path = request.url.path
        elapsed = time.time() - _startup_time
        if (elapsed < _STARTUP_GRACE_SECONDS
                and path not in _STARTUP_PASSTHROUGH
                and not any(path.startswith(p) for p in _STARTUP_PASSTHROUGH_PREFIXES)):
            accept = request.headers.get("accept", "")
            if "text/html" in accept:
                return HTMLResponse(
                    "<html><head><meta http-equiv='refresh' content='3'></head>"
                    "<body style='font-family:sans-serif;text-align:center;padding:80px 20px;'>"
                    "<h2 style='color:#ec4899;'>Spark запускается…</h2>"
                    "<p style='color:#6b7280;'>Пожалуйста, подождите несколько секунд.</p>"
                    "</body></html>",
                    status_code=503,
                    headers={"Retry-After": "3"},
                )
            return JSONResponse(
                {"detail": "Service is starting, please retry in a few seconds."},
                status_code=503,
                headers={"Retry-After": "3"},
            )
    return await call_next(request)


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


def _fix_broken_photo_urls() -> None:
    """
    Startup check: photos are stored as base64 data URLs in the DB.
    Any leftover /photos/xxx.jpg URLs where the file no longer exists
    on disk (e.g. after a Railway redeploy without a Volume) are cleared
    to NULL so the UI shows the 👤 placeholder instead of a broken image.
    """
    from sqlalchemy import text as _t

    photo_dir = Path(_PHOTO_DIR)

    def _is_broken(url: str | None) -> bool:
        if not url:
            return False
        if url.startswith("data:image/"):
            return False  # base64 data URLs are always valid
        if url.startswith("/photos/"):
            fname = url.split("/")[-1]
            return not (photo_dir / fname).exists()
        return False  # external URLs or unknown — leave as-is

    try:
        with engine.begin() as conn:
            # Only load file-path URLs (NOT data: URLs) to avoid reading megabytes of
            # base64 photo data into memory — base64 URLs are never broken anyway.
            rows = conn.execute(_t(
                "SELECT id, photo FROM profiles "
                "WHERE photo IS NOT NULL AND photo NOT LIKE 'data:%'"
            )).fetchall()
            fixed = 0
            for row_id, photo in rows:
                if _is_broken(photo):
                    conn.execute(_t("UPDATE profiles SET photo=NULL WHERE id=:id"), {"id": row_id})
                    fixed += 1
            if fixed:
                logging.info("fix_photos: cleared %d broken profile photo URLs", fixed)

            rows2 = conn.execute(_t(
                "SELECT id, url FROM profile_photos "
                "WHERE url IS NOT NULL AND url NOT LIKE 'data:%'"
            )).fetchall()
            fixed2 = 0
            for row_id, url in rows2:
                if _is_broken(url):
                    conn.execute(_t("DELETE FROM profile_photos WHERE id=:id"), {"id": row_id})
                    fixed2 += 1
            if fixed2:
                logging.info("fix_photos: removed %d broken gallery photo rows", fixed2)

            # Stories with file-based images are broken after redeploy — delete them
            # (they expire in 24h anyway; new stories use base64)
            try:
                res3 = conn.execute(_t(
                    "DELETE FROM stories WHERE media_type='image' AND content LIKE '/photos/%'"
                ))
                if res3.rowcount:
                    logging.info("fix_photos: removed %d broken story image rows", res3.rowcount)
            except Exception:
                pass
    except Exception as _e:
        logging.warning("fix_broken_photo_urls: %s", _e)



def _do_cleanup() -> None:
    """Periodic DB housekeeping: expired boosts, old views, unverified accounts, expired stories."""
    from sqlalchemy import text as _t
    now = _utcnow()
    try:
        with engine.begin() as conn:
            r = conn.execute(_t(
                "UPDATE users SET boost_until = NULL WHERE boost_until IS NOT NULL AND boost_until < :now"
            ), {"now": now})
            if r.rowcount:
                logging.info("cleanup: cleared %d expired boosts", r.rowcount)

            cutoff_views = now - timedelta(days=30)
            r = conn.execute(_t(
                "DELETE FROM profile_views WHERE created_at < :cutoff"
            ), {"cutoff": cutoff_views})
            if r.rowcount:
                logging.info("cleanup: deleted %d old profile views", r.rowcount)

            cutoff_unverified = now - timedelta(days=7)
            r = conn.execute(_t(
                "DELETE FROM users WHERE is_active = FALSE AND created_at < :cutoff"
            ), {"cutoff": cutoff_unverified})
            if r.rowcount:
                logging.info("cleanup: deleted %d stale unverified accounts", r.rowcount)

            try:
                cutoff_stories = now - timedelta(hours=24)
                r = conn.execute(_t(
                    "DELETE FROM stories WHERE created_at < :cutoff"
                ), {"cutoff": cutoff_stories})
                if r.rowcount:
                    logging.info("cleanup: deleted %d expired stories", r.rowcount)
            except Exception:
                pass
    except Exception as _e:
        logging.warning("_do_cleanup: %s", _e)


async def _periodic_cleanup() -> None:
    """Background loop: run housekeeping every hour. Starts 5 min after startup."""
    await asyncio.sleep(300)
    while True:
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, _do_cleanup)
        except Exception as _e:
            logging.error("periodic_cleanup: unexpected error: %s", _e, exc_info=True)
        await asyncio.sleep(3600)


# HIGH-6: Reject oversized request bodies before they reach route handlers.
# Prevents DoS via 100 MB audio/image uploads buffered into memory.
_MAX_BODY_BYTES = 12 * 1024 * 1024  # 12 MB ceiling

@app.middleware("http")
async def max_body_size_middleware(request: Request, call_next):
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            cl_int = int(content_length)
        except (ValueError, TypeError):
            return JSONResponse({"detail": "Invalid Content-Length"}, status_code=400)
        if cl_int > _MAX_BODY_BYTES:
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
        _secure = bool(os.getenv("RAILWAY_ENVIRONMENT") or os.getenv("SECURE_COOKIES"))
        response.set_cookie(
            _CSRF_COOKIE, csrf_token,
            httponly=False, samesite="lax", max_age=60 * 60 * 24 * 7, secure=_secure,
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
    # Prevent HTML pages from being cached — critical for Alpine.js state freshness
    if response.headers.get("content-type", "").startswith("text/html"):
        response.headers.setdefault("Cache-Control", "no-cache, no-store, must-revalidate")
        response.headers.setdefault("Pragma", "no-cache")
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
app.include_router(push_router.router)
app.include_router(admin_router.router)


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
    _record_error(request.method, request.url.path, exc, tb)
    asyncio.create_task(_send_error_email(request.method, request.url.path, exc, tb))
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


@app.get("/sw.js", include_in_schema=False)
def service_worker():
    return FileResponse(
        "static/sw.js",
        media_type="application/javascript",
        headers={"Service-Worker-Allowed": "/", "Cache-Control": "no-cache"},
    )


@app.get("/health")
def health():
    db_ok = False
    try:
        with engine.connect() as conn:
            conn.execute(_text("SELECT 1"))
        db_ok = True
    except Exception as e:
        logging.warning("Health check: DB not ready — %s", e)
    return JSONResponse(
        status_code=200,
        content={
            "status": "ok" if db_ok else "starting",
            "db": db_ok,
            "startup_done": _startup_done,
            "startup_ok": _startup_ok,
        },
    )


@app.get("/metrics")
def app_metrics(token: str = Query(default="")):
    required = os.getenv("METRICS_TOKEN", "")
    if not required or token != required:
        raise HTTPException(403, "Forbidden")
    with _m_lock:
        return JSONResponse({
            "uptime_seconds": int(time.time() - _m["started_at"]),
            "requests_total": _m["requests_total"],
            "errors_5xx": _m["errors_5xx"],
            "status_counts": dict(_m["status_counts"]),
        })


@app.get("/errors")
def app_errors(token: str = Query(default="")):
    required = os.getenv("METRICS_TOKEN", "")
    if not required or token != required:
        raise HTTPException(403, "Forbidden")
    with _m_lock:
        return JSONResponse({"errors": list(_error_log)})


@app.get("/sentry-debug/")
@app.get("/sentry-debug")
async def sentry_debug():
    raise RuntimeError("Sentry debug: error tracking is working!")


@app.get("/privacy", response_class=HTMLResponse)
def privacy(request: Request):
    return templates.TemplateResponse(request, "privacy.html", {})


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
