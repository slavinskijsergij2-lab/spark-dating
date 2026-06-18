import json
import logging
import os
import traceback
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

logging.basicConfig(level=logging.INFO)

from app.database import Base, engine
from app.i18n import get_lang, get_translations, is_rtl
from app.routers import auth, profile, swipe, matches
from app.routers import features
from app.templates import templates

Base.metadata.create_all(bind=engine)

# Inline DB migration — safe for PostgreSQL (IF NOT EXISTS) and SQLite (try/except)
from sqlalchemy import text as _text
_is_pg = str(engine.url).startswith("postgresql")
_migrations = [
    "ALTER TABLE users ADD COLUMN{} last_seen TIMESTAMP",
    "ALTER TABLE users ADD COLUMN{} email_verified BOOLEAN NOT NULL DEFAULT TRUE",
    "ALTER TABLE users ADD COLUMN{} email_verify_token VARCHAR(100)",
    "ALTER TABLE matches ADD COLUMN{} seen_by_user1 BOOLEAN NOT NULL DEFAULT TRUE",
    "ALTER TABLE matches ADD COLUMN{} seen_by_user2 BOOLEAN NOT NULL DEFAULT FALSE",
]
for _m in _migrations:
    _sql = _m.format(" IF NOT EXISTS" if _is_pg else "")
    try:
        with engine.begin() as _c:
            _c.execute(_text(_sql))
    except Exception:
        pass

Path("static/uploads").mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Spark — сайт знакомств")
app.mount("/static", StaticFiles(directory="static"), name="static")


def _tojson(value, indent=None):
    def default(o):
        if isinstance(o, datetime):
            return o.isoformat()
        raise TypeError(f"Object of type {type(o)} is not JSON serializable")
    return json.dumps(value, default=default, indent=indent, ensure_ascii=False)


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
    diff = (datetime.utcnow() - last_seen).total_seconds()
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

app.include_router(auth.router)
app.include_router(profile.router)
app.include_router(swipe.router)
app.include_router(matches.router)
app.include_router(features.router)


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    if exc.status_code == 401 and "text/html" in request.headers.get("accept", ""):
        response = RedirectResponse("/login", status_code=302)
        response.delete_cookie("access_token")
        return response
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    tb = traceback.format_exc()
    logging.error("Unhandled exception on %s %s:\n%s", request.method, request.url.path, tb)
    return JSONResponse(status_code=500, content={"error": str(exc), "type": type(exc).__name__})


@app.get("/health")
def health():
    from app.database import engine
    try:
        with engine.connect() as conn:
            conn.execute(__import__("sqlalchemy").text("SELECT 1"))
        return {"status": "ok", "db": str(engine.url).split("@")[-1]}
    except Exception as e:
        return JSONResponse(status_code=500, content={"status": "error", "detail": str(e)})


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    token = request.cookies.get("access_token")
    if token:
        return RedirectResponse("/swipe", status_code=302)
    lang = get_lang(request)
    return templates.TemplateResponse("index.html", {
        "request": request,
        "t": get_translations(lang),
        "rtl": is_rtl(lang),
    })
