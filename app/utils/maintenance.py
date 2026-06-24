"""Startup checks and periodic DB housekeeping.

Called from main.py lifespan — always run in a thread pool executor so
they never block the async event loop.
"""
import logging
import os
from datetime import timedelta
from pathlib import Path

from app.utils.time import utcnow as _utcnow


def _photo_dir() -> Path:
    return Path(os.getenv("PHOTO_DIR", "static/photos"))


def fix_broken_photo_urls() -> None:
    """Clear profile/gallery/story photo URLs that point to missing files.

    Runs once at startup.  After a Railway redeploy without a persistent
    Volume the files are gone — this prevents broken <img> tags.
    """
    from sqlalchemy import text as _t
    from app.database import engine

    photo_dir = _photo_dir()

    def _missing(url: str | None) -> bool:
        if not url or url.startswith("data:image/"):
            return False
        if url.startswith("/photos/"):
            return not (photo_dir / url.split("/")[-1]).exists()
        return False

    try:
        with engine.begin() as conn:
            rows = conn.execute(_t(
                "SELECT id, photo FROM profiles "
                "WHERE photo IS NOT NULL AND photo NOT LIKE 'data:%'"
            )).fetchall()
            fixed = 0
            for row_id, photo in rows:
                if _missing(photo):
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
                if _missing(url):
                    conn.execute(_t("DELETE FROM profile_photos WHERE id=:id"), {"id": row_id})
                    fixed2 += 1
            if fixed2:
                logging.info("fix_photos: removed %d broken gallery photo rows", fixed2)

            try:
                r3 = conn.execute(_t(
                    "DELETE FROM stories WHERE media_type='image' AND content LIKE '/photos/%'"
                ))
                if r3.rowcount:
                    logging.info("fix_photos: removed %d broken story image rows", r3.rowcount)
            except Exception:
                pass
    except Exception as exc:
        logging.warning("fix_broken_photo_urls: %s", exc)


def do_cleanup() -> None:
    """Periodic housekeeping: expired boosts, old views, unverified accounts,
    expired stories, and old error_logs rows."""
    from sqlalchemy import text as _t
    from app.database import engine

    now = _utcnow()
    try:
        with engine.begin() as conn:
            r = conn.execute(_t(
                "UPDATE users SET boost_until = NULL "
                "WHERE boost_until IS NOT NULL AND boost_until < :now"
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

            try:
                cutoff_errors = now - timedelta(days=30)
                r = conn.execute(_t(
                    "DELETE FROM error_logs WHERE ts < :cutoff"
                ), {"cutoff": cutoff_errors})
                if r.rowcount:
                    logging.info("cleanup: deleted %d old error_log rows", r.rowcount)
            except Exception:
                pass

    except Exception as exc:
        logging.warning("do_cleanup: %s", exc)
