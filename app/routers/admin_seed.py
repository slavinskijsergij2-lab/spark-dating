"""Temporary admin endpoint to seed test bots. Remove after use."""
import base64
import io
import os

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.auth import hash_password
from app.models.models import GenderEnum, Profile, User
from app.utils.time import utcnow as _utcnow

router = APIRouter()

_SEED_KEY = os.getenv("SEED_KEY", "spark-seed-2024")

BOTS = [
    dict(email="anna_bot@spark.test",   name="Анна",    age=24, gender=GenderEnum.female, city="Київ",   bio="Люблю подорожі, кіно та каву ☕ Шукаю когось особливого", interests="кіно,подорожі,кава,йога",         intention="serious",  color=(236,72,153)),
    dict(email="masha_bot@spark.test",  name="Маша",    age=27, gender=GenderEnum.female, city="Москва", bio="Дизайнер, обожаю музыку и горы 🏔️ Ищу приключения",       interests="музыка,горы,дизайн,путешествия",   intention="casual",   color=(167,139,250)),
    dict(email="kate_bot@spark.test",   name="Катя",    age=22, gender=GenderEnum.female, city="Одеса",  bio="Студентка медицини, займаюсь танцями 💃",                  interests="танці,медицина,море,книги",        intention="browsing", color=(251,146,60)),
    dict(email="sofia_bot@spark.test",  name="Софія",   age=29, gender=GenderEnum.female, city="Львів",  bio="Психолог, люблю мистецтво та тихі вечори 🎨",              interests="психологія,мистецтво,живопис,вино",intention="serious",  color=(34,197,94)),
    dict(email="alina_bot@spark.test",  name="Аліна",   age=25, gender=GenderEnum.female, city="Харків", bio="IT-менеджер. Спорт, сауна, смачна їжа 🍕",                interests="спорт,IT,їжа,сауна",               intention="today",    color=(6,182,212)),
    dict(email="dmitry_bot@spark.test", name="Дмитро",  age=28, gender=GenderEnum.male,   city="Київ",   bio="Підприємець. Люблю мотоцикли та рибалку 🎣",               interests="мотоцикли,рибалка,бізнес,гори",    intention="serious",  color=(59,130,246)),
    dict(email="alex_bot@spark.test",   name="Олексій", age=31, gender=GenderEnum.male,   city="Дніпро", bio="Програміст, люблю хайкінг і готувати 🍳",                  interests="програмування,хайкінг,кулінарія",  intention="casual",   color=(16,185,129)),
    dict(email="max_bot@spark.test",    name="Максим",  age=26, gender=GenderEnum.male,   city="Одеса",  bio="Фотограф та відеограф 📷 Море — моя стихія",               interests="фотографія,море,відео,подорожі",   intention="browsing", color=(245,158,11)),
]


def _make_avatar(initials: str, bg: tuple, size: int = 300) -> str:
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        return ""
    img = Image.new("RGB", (size, size), bg)
    draw = ImageDraw.Draw(img)
    m = size // 6
    draw.ellipse([m, m, size - m, size - m], fill=tuple(min(255, c + 50) for c in bg))
    font_size = size // 3
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", font_size)
    except Exception:
        try:
            font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", font_size)
        except Exception:
            font = ImageFont.load_default()
    bbox = draw.textbbox((0, 0), initials, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text(((size - tw) // 2, (size - th) // 2 - bbox[1] // 2),
              initials, fill=(255, 255, 255), font=font)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=82)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


@router.get("/admin/seed-bots")
def seed_bots(key: str = "", db: Session = Depends(get_db)):
    if key != _SEED_KEY:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    created, skipped = [], []
    pw = hash_password("BotPassword123!")

    for b in BOTS:
        if db.query(User).filter(User.email == b["email"]).first():
            skipped.append(b["name"])
            continue

        user = User(
            email=b["email"],
            hashed_password=pw,
            language="ru",
            is_active=True,
            email_verified=True,
            last_seen=_utcnow(),
        )
        db.add(user)
        db.flush()

        photo = _make_avatar(b["name"][0], b["color"])
        db.add(Profile(
            user_id=user.id,
            name=b["name"], age=b["age"], gender=b["gender"],
            city=b["city"], bio=b["bio"],
            photo=photo if photo else None,
            interests=b["interests"], intention=b["intention"],
        ))
        db.commit()
        created.append(b["name"])

    return JSONResponse({
        "created": created,
        "skipped": skipped,
        "password": "BotPassword123!",
    })
