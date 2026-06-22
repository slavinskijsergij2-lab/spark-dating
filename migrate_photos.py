"""
Однократная миграция: конвертирует base64-фото из PostgreSQL в файлы на диске.

Запуск:
    python migrate_photos.py

Требования:
    - Переменные окружения из .env должны быть доступны
    - Если используется Railway Volume — установи PHOTO_DIR=/data/photos перед запуском
    - После миграции все старые data:image/... заменяются на /photos/<uuid>.jpg
"""

import base64
import os
import sys
import uuid
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from app.database import SessionLocal
from app.models.models import Profile, ProfilePhoto, Story

PHOTO_DIR = Path(os.getenv("PHOTO_DIR", "static/photos"))
PHOTO_DIR.mkdir(parents=True, exist_ok=True)


def _save_b64(b64_str: str) -> str:
    """Декодирует data URI, пишет файл, возвращает URL /photos/<uuid>.jpg."""
    if not b64_str or not b64_str.startswith("data:image/"):
        return b64_str  # уже URL или None — не трогаем

    try:
        _header, data = b64_str.split(",", 1)
        raw = base64.b64decode(data)
    except Exception as e:
        print(f"  [skip] не удалось декодировать base64: {e}")
        return b64_str

    filename = f"{uuid.uuid4().hex}.jpg"
    (PHOTO_DIR / filename).write_bytes(raw)
    return f"/photos/{filename}"


def main():
    db = SessionLocal()
    errors = 0

    try:
        # ── Главные фото профилей ──────────────────────────────────────────
        profiles = db.query(Profile).filter(
            Profile.photo.like("data:image/%")
        ).all()
        print(f"Профили с base64-фото: {len(profiles)}")
        for p in profiles:
            try:
                p.photo = _save_b64(p.photo)
            except Exception as e:
                print(f"  [error] profile_id={p.id}: {e}")
                errors += 1
        db.commit()
        print("  ✓ Главные фото профилей сохранены")

        # ── Галерея (доп. фото) ────────────────────────────────────────────
        gallery = db.query(ProfilePhoto).filter(
            ProfilePhoto.url.like("data:image/%")
        ).all()
        print(f"Фото галереи с base64: {len(gallery)}")
        for p in gallery:
            try:
                p.url = _save_b64(p.url)
            except Exception as e:
                print(f"  [error] profile_photo_id={p.id}: {e}")
                errors += 1
        db.commit()
        print("  ✓ Галерея сохранена")

        # ── Изображения историй ────────────────────────────────────────────
        stories = db.query(Story).filter(
            Story.media_type == "image",
            Story.content.like("data:image/%"),
        ).all()
        print(f"Истории с base64-изображением: {len(stories)}")
        for s in stories:
            try:
                s.content = _save_b64(s.content)
            except Exception as e:
                print(f"  [error] story_id={s.id}: {e}")
                errors += 1
        db.commit()
        print("  ✓ Истории сохранены")

    finally:
        db.close()

    total = len(profiles) + len(gallery) + len(stories)
    if errors:
        print(f"\n⚠️  Завершено с ошибками: {errors}/{total} записей не удалось обработать")
        sys.exit(1)
    else:
        print(f"\n✅ Миграция завершена: {total} записей переведено в файлы → {PHOTO_DIR}")


if __name__ == "__main__":
    main()
