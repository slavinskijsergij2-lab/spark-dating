import json
import os
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

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
