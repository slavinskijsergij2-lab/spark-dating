"""
Seed script — creates test bot profiles so you can swipe on them.
Run locally:
  python3 seed_bots.py
Or against Railway DB:
  railway run python3 seed_bots.py
"""
import io
import os
import sys
import uuid
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

from PIL import Image, ImageDraw, ImageFont

from app.database import Base, engine, SessionLocal
from app.auth import hash_password
from app.models.models import GenderEnum, Profile, User
from app.utils.time import utcnow as _utcnow

Base.metadata.create_all(bind=engine)

_PHOTO_DIR = Path(os.getenv("PHOTO_DIR", "static/photos"))
_PHOTO_DIR.mkdir(parents=True, exist_ok=True)


def make_avatar(initials: str, bg: tuple, size: int = 400) -> str:
    """Generate a colored avatar, save to PHOTO_DIR, return /photos/... URL."""
    img = Image.new("RGB", (size, size), bg)
    draw = ImageDraw.Draw(img)
    margin = size // 6
    draw.ellipse(
        [margin, margin, size - margin, size - margin],
        fill=tuple(min(255, c + 40) for c in bg),
    )
    font_size = size // 3
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", font_size)
    except Exception:
        try:
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", font_size)
        except Exception:
            font = ImageFont.load_default()
    bbox = draw.textbbox((0, 0), initials, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text(
        ((size - tw) // 2, (size - th) // 2 - bbox[1] // 2),
        initials, fill=(255, 255, 255, 220), font=font,
    )
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=85)

    filename = f"bot_{uuid.uuid4().hex}.jpg"
    (_PHOTO_DIR / filename).write_bytes(buf.getvalue())
    return f"/photos/{filename}"


BOTS = [
    # female bots (appear for male users looking for female)
    dict(
        email="anna_bot@spark.test",
        name="Анна", age=24,
        gender=GenderEnum.female, looking_for=GenderEnum.male,
        city="Київ", bio="Люблю подорожі, кіно та каву ☕ Шукаю когось особливого",
        interests="кіно,подорожі,кава,йога", intention="serious",
        bg=(236, 72, 153),
    ),
    dict(
        email="masha_bot@spark.test",
        name="Маша", age=27,
        gender=GenderEnum.female, looking_for=GenderEnum.male,
        city="Москва", bio="Дизайнер, обожаю музыку и горы 🏔️ Ищу приключения",
        interests="музыка,горы,дизайн,путешествия", intention="casual",
        bg=(167, 139, 250),
    ),
    dict(
        email="kate_bot@spark.test",
        name="Катя", age=22,
        gender=GenderEnum.female, looking_for=GenderEnum.male,
        city="Одеса", bio="Студентка медицини, займаюсь танцями 💃",
        interests="танці,медицина,море,книги", intention="browsing",
        bg=(251, 146, 60),
    ),
    dict(
        email="sofia_bot@spark.test",
        name="Софія", age=29,
        gender=GenderEnum.female, looking_for=GenderEnum.male,
        city="Львів", bio="Психолог, люблю мистецтво та тихі вечори 🎨",
        interests="психологія,мистецтво,живопис,вино", intention="serious",
        bg=(34, 197, 94),
    ),
    dict(
        email="alina_bot@spark.test",
        name="Аліна", age=25,
        gender=GenderEnum.female, looking_for=GenderEnum.male,
        city="Харків", bio="IT-менеджер. Спорт, сауна, смачна їжа 🍕",
        interests="спорт,IT,їжа,сауна", intention="today",
        bg=(6, 182, 212),
    ),
    # male bots (appear for female users looking for male)
    dict(
        email="dmitry_bot@spark.test",
        name="Дмитро", age=28,
        gender=GenderEnum.male, looking_for=GenderEnum.female,
        city="Київ", bio="Підприємець. Люблю мотоцикли та рибалку 🎣",
        interests="мотоцикли,рибалка,бізнес,гори", intention="serious",
        bg=(59, 130, 246),
    ),
    dict(
        email="alex_bot@spark.test",
        name="Олексій", age=31,
        gender=GenderEnum.male, looking_for=GenderEnum.female,
        city="Дніпро", bio="Програміст, люблю хайкінг і готувати 🍳",
        interests="програмування,хайкінг,кулінарія,музика", intention="casual",
        bg=(16, 185, 129),
    ),
    dict(
        email="max_bot@spark.test",
        name="Максим", age=26,
        gender=GenderEnum.male, looking_for=GenderEnum.female,
        city="Одеса", bio="Фотограф та відеограф 📷 Море — моя стихія",
        interests="фотографія,море,відео,подорожі", intention="browsing",
        bg=(245, 158, 11),
    ),
]


def seed():
    db = SessionLocal()
    created = 0
    skipped = 0

    for b in BOTS:
        existing = db.query(User).filter(User.email == b["email"]).first()
        if existing:
            # Update looking_for on existing bots (fix for old seeds without it)
            profile = db.query(Profile).filter(Profile.user_id == existing.id).first()
            if profile and profile.looking_for is None:
                profile.looking_for = b["looking_for"]
                db.commit()
                print(f"  upd   {b['name']} — обновлён looking_for")
            else:
                print(f"  skip  {b['name']} ({b['email']}) — уже существует")
            skipped += 1
            continue

        photo_url = make_avatar(b["name"][0].upper(), b["bg"])

        user = User(
            email=b["email"],
            hashed_password=hash_password("BotPassword123!"),
            language="ru",
            is_active=True,
            email_verified=True,
            last_seen=_utcnow(),
        )
        db.add(user)
        db.flush()

        profile = Profile(
            user_id=user.id,
            name=b["name"],
            age=b["age"],
            gender=b["gender"],
            looking_for=b["looking_for"],
            city=b.get("city"),
            bio=b.get("bio"),
            photo=photo_url,
            interests=b.get("interests"),
            intention=b.get("intention"),
        )
        db.add(profile)
        db.commit()
        print(f"  ✓     {b['name']} ({b['email']}) — создан, фото: {photo_url}")
        created += 1

    db.close()
    print(f"\nГотово: создано {created}, пропущено {skipped}")
    print("Пароль всех ботов: BotPassword123!")


if __name__ == "__main__":
    print(f"Создаю тестовых ботов (фото → {_PHOTO_DIR})...\n")
    seed()
