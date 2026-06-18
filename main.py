import json
import logging
import os
import traceback
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

logging.basicConfig(level=logging.INFO)

from app.database import Base, engine
from app.i18n import get_lang, get_translations, is_rtl
from app.routers import auth, profile, swipe, matches
from app.routers import features

Base.metadata.create_all(bind=engine)

Path("static/uploads").mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Spark — сайт знакомств")
app.mount("/static", StaticFiles(directory="static"), name="static")

templates = Jinja2Templates(directory="templates")


def _tojson(value, indent=None):
    def default(o):
        if isinstance(o, datetime):
            return o.isoformat()
        raise TypeError(f"Object of type {type(o)} is not JSON serializable")
    return json.dumps(value, default=default, indent=indent, ensure_ascii=False)


templates.env.filters["tojson"] = _tojson

app.include_router(auth.router)
app.include_router(profile.router)
app.include_router(swipe.router)
app.include_router(matches.router)
app.include_router(features.router)


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
