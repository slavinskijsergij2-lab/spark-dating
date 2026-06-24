#!/usr/bin/env python3
"""Migrate base64-encoded chat photo messages to filesystem.

Run on Railway after mounting Volume:
    PHOTO_DIR=/data/photos DATABASE_URL=<prod_url> python scripts/migrate_chat_photos.py

Dry-run (no writes):
    DRY_RUN=1 ... python scripts/migrate_chat_photos.py
"""
import base64
import os
import sys
import uuid
from pathlib import Path

# Must be set before app imports
DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    print("ERROR: DATABASE_URL is not set", file=sys.stderr)
    sys.exit(1)

PHOTO_DIR = os.environ.get("PHOTO_DIR")
if not PHOTO_DIR:
    print("ERROR: PHOTO_DIR is not set", file=sys.stderr)
    sys.exit(1)

DRY_RUN = os.environ.get("DRY_RUN") == "1"

os.environ.setdefault("SECRET_KEY", "migrate-script")

import sqlalchemy as sa
from sqlalchemy.orm import Session

engine = sa.create_engine(DATABASE_URL.replace("postgresql+asyncpg", "postgresql"))
photo_dir = Path(PHOTO_DIR)
photo_dir.mkdir(parents=True, exist_ok=True)


def run():
    with Session(engine) as session:
        rows = session.execute(
            sa.text(
                "SELECT id, content FROM messages WHERE is_image = true AND content LIKE 'data:%'"
            )
        ).fetchall()

        print(f"Found {len(rows)} base64 image messages to migrate")
        if DRY_RUN:
            print("DRY_RUN=1 — no changes will be made")

        migrated = 0
        errors = 0

        for row in rows:
            msg_id, content = row
            try:
                # Parse data URL: data:<mime>;base64,<data>
                header, b64_data = content.split(",", 1)
                raw = base64.b64decode(b64_data)

                # Compress with PIL
                from PIL import Image, ImageOps
                import io as _io
                img = Image.open(_io.BytesIO(raw))
                img = ImageOps.exif_transpose(img)
                img.thumbnail((800, 800), Image.LANCZOS)
                img = img.convert("RGB")
                buf = _io.BytesIO()
                img.save(buf, "JPEG", quality=80)
                compressed = buf.getvalue()

                filename = f"chat_{uuid.uuid4().hex}.jpg"
                file_path = photo_dir / filename
                new_url = f"/photos/{filename}"

                if not DRY_RUN:
                    file_path.write_bytes(compressed)
                    session.execute(
                        sa.text("UPDATE messages SET content = :url WHERE id = :id"),
                        {"url": new_url, "id": msg_id},
                    )

                original_kb = len(raw) // 1024
                new_kb = len(compressed) // 1024
                print(f"  msg {msg_id}: {original_kb}KB → {new_kb}KB → {new_url}")
                migrated += 1

            except Exception as exc:
                print(f"  msg {msg_id}: ERROR — {exc}", file=sys.stderr)
                errors += 1

        if not DRY_RUN:
            session.commit()
            print(f"\nDone: {migrated} migrated, {errors} errors")
        else:
            print(f"\nDry run complete: {migrated} would be migrated, {errors} errors")


if __name__ == "__main__":
    run()
