#!/usr/bin/env python3
"""Migrate base64-encoded voice messages to filesystem.

Usage (on Railway after mounting Volume):
    PHOTO_DIR=/data/photos DATABASE_URL=<prod_url> python scripts/migrate_voice_messages.py

Dry-run (no writes):
    DRY_RUN=1 ... python scripts/migrate_voice_messages.py
"""
import base64
import os
import sys
import uuid
from pathlib import Path

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

_EXT_MAP = {
    "audio/webm": "webm",
    "audio/ogg":  "ogg",
    "audio/mp4":  "m4a",
    "audio/mpeg": "mp3",
    "audio/wav":  "wav",
}


def run():
    with Session(engine) as session:
        rows = session.execute(
            sa.text(
                "SELECT id, content FROM messages WHERE is_voice = true AND content LIKE 'data:%'"
            )
        ).fetchall()

        print(f"Found {len(rows)} base64 voice messages to migrate")
        if DRY_RUN:
            print("DRY_RUN=1 — no changes will be made")

        migrated = 0
        errors = 0

        for row in rows:
            msg_id, content = row
            try:
                # Parse: data:<mime>;base64,<data>
                header, b64_data = content.split(",", 1)
                mime = header.split(":")[1].split(";")[0].strip()
                raw = base64.b64decode(b64_data)

                ext = _EXT_MAP.get(mime, "webm")
                filename = f"voice_{uuid.uuid4().hex}.{ext}"
                file_path = photo_dir / filename
                new_url = f"/photos/{filename}"

                if not DRY_RUN:
                    file_path.write_bytes(raw)
                    session.execute(
                        sa.text("UPDATE messages SET content = :url WHERE id = :id"),
                        {"url": new_url, "id": msg_id},
                    )

                kb = len(raw) // 1024
                print(f"  msg {msg_id}: {mime} {kb}KB → {new_url}")
                migrated += 1

            except Exception as exc:
                print(f"  msg {msg_id}: ERROR — {exc}", file=sys.stderr)
                errors += 1

        if not DRY_RUN:
            session.commit()
            print(f"\nDone: {migrated} migrated, {errors} errors")
        else:
            print(f"\nDry run: {migrated} would be migrated, {errors} errors")


if __name__ == "__main__":
    run()
