"""Add missing indexes, UniqueConstraint on stories, token_version, ON DELETE CASCADE (PG only)

Revision ID: 003
Revises: 002
Create Date: 2026-06-22
"""
from alembic import op
import sqlalchemy as sa
import logging

revision = "003"
down_revision = "002"
branch_labels = None
depends_on = None

_log = logging.getLogger(__name__)


def _conn():
    """Return the current migration connection (Alembic 1.7+ style)."""
    return op.get_context().connection


def _insp():
    """Return an Inspector for the current connection."""
    return sa.inspect(_conn())


def _is_pg() -> bool:
    return _conn().dialect.name == "postgresql"


def _table_exists(table: str) -> bool:
    return table in _insp().get_table_names()


def _index_exists(name: str, table: str) -> bool:
    if not _table_exists(table):
        return False
    return any(idx["name"] == name for idx in _insp().get_indexes(table))


def _column_exists(table: str, column: str) -> bool:
    if not _table_exists(table):
        return False
    return any(c["name"] == column for c in _insp().get_columns(table))


def _unique_exists(table: str, name: str) -> bool:
    if not _table_exists(table):
        return False
    return any(u["name"] == name for u in _insp().get_unique_constraints(table))


def _safe_exec(label: str, fn, critical: bool = False) -> None:
    """Run fn() inside a SAVEPOINT so a PG error doesn't abort the outer tx."""
    conn = _conn()
    is_pg = conn.dialect.name == "postgresql"
    if is_pg:
        conn.execute(sa.text(f"SAVEPOINT sp_{label}"))
    try:
        fn()
        if is_pg:
            conn.execute(sa.text(f"RELEASE SAVEPOINT sp_{label}"))
    except Exception as exc:
        if is_pg:
            conn.execute(sa.text(f"ROLLBACK TO SAVEPOINT sp_{label}"))
        if critical:
            _log.error("003: CRITICAL failure in %s: %s", label, exc)
            raise
        _log.warning("003: skip %s: %s", label, exc)


def upgrade() -> None:
    # ── new indexes — each in its own SAVEPOINT so one failure can't abort tx ─
    for idx_name, tbl, cols in [
        ("ix_like_liked_is_like",  "likes",         ["liked_id", "is_like"]),
        ("ix_profile_swipe",       "profiles",      ["gender", "age", "intention"]),
        ("ix_user_last_seen",      "users",         ["last_seen"]),
        ("ix_story_expires_at",    "stories",       ["expires_at"]),
        ("ix_profile_view_viewed", "profile_views", ["viewed_id", "viewer_id", "created_at"]),
    ]:
        _n, _t, _c = idx_name, tbl, cols
        _safe_exec(idx_name, lambda: (
            op.create_index(_n, _t, _c) if not _index_exists(_n, _t) else None
        ))

    # ── token_version column — CRITICAL: auth breaks without this ─────────────
    def _add_token_version():
        if not _column_exists("users", "token_version"):
            op.add_column("users", sa.Column(
                "token_version", sa.Integer(), nullable=False, server_default="0"
            ))
    _safe_exec("token_version", _add_token_version, critical=True)

    # ── story: deduplicate then add unique constraint ──────────────────────────
    try:
        if _table_exists("stories"):
            op.execute("""
                DELETE FROM stories
                WHERE id NOT IN (
                    SELECT MAX(id) FROM stories GROUP BY user_id
                )
            """)
            if _is_pg() and not _unique_exists("stories", "uq_story_per_user"):
                op.create_unique_constraint("uq_story_per_user", "stories", ["user_id"])
    except Exception as exc:
        _log.warning("003: skip story dedup/constraint: %s", exc)

    # ── check constraint: user1_id < user2_id in matches (PostgreSQL only) ────
    if _is_pg():
        try:
            op.create_check_constraint(
                "ck_match_user_order", "matches", "user1_id < user2_id"
            )
        except Exception:
            pass  # already exists or unsupported

    # ── clean up old expired story rows ──────────────────────────────────────
    try:
        if _table_exists("stories"):
            if _is_pg():
                op.execute("DELETE FROM stories WHERE expires_at < NOW() - INTERVAL '1 day'")
            else:
                op.execute("DELETE FROM stories WHERE expires_at < datetime('now', '-1 day')")
    except Exception as exc:
        _log.warning("003: skip expired story cleanup: %s", exc)

    # ── ON DELETE CASCADE (PostgreSQL only — SQLite ignores FK constraints) ───
    if _is_pg():
        try:
            _recreate_fks()
        except Exception as exc:
            _log.warning("003: FK recreation skipped (non-critical): %s", exc)


def _recreate_fks() -> None:
    """Drop and recreate foreign keys with ON DELETE CASCADE on PostgreSQL."""
    pairs = [
        ("profiles",         "profiles_user_id_fkey",            "users",    ["user_id"],       ["id"], "CASCADE"),
        ("likes",            "likes_liker_id_fkey",              "users",    ["liker_id"],      ["id"], "CASCADE"),
        ("likes",            "likes_liked_id_fkey",              "users",    ["liked_id"],      ["id"], "CASCADE"),
        ("matches",          "matches_user1_id_fkey",            "users",    ["user1_id"],      ["id"], "CASCADE"),
        ("matches",          "matches_user2_id_fkey",            "users",    ["user2_id"],      ["id"], "CASCADE"),
        ("messages",         "messages_match_id_fkey",           "matches",  ["match_id"],      ["id"], "CASCADE"),
        ("messages",         "messages_sender_id_fkey",          "users",    ["sender_id"],     ["id"], "CASCADE"),
        ("message_reactions","message_reactions_message_id_fkey","messages", ["message_id"],    ["id"], "CASCADE"),
        ("message_reactions","message_reactions_user_id_fkey",   "users",    ["user_id"],       ["id"], "CASCADE"),
        ("blocks",           "blocks_blocker_id_fkey",           "users",    ["blocker_id"],    ["id"], "CASCADE"),
        ("blocks",           "blocks_blocked_id_fkey",           "users",    ["blocked_id"],    ["id"], "CASCADE"),
        ("reports",          "reports_reporter_id_fkey",         "users",    ["reporter_id"],   ["id"], "CASCADE"),
        ("reports",          "reports_reported_id_fkey",         "users",    ["reported_id"],   ["id"], "CASCADE"),
        ("stories",          "stories_user_id_fkey",             "users",    ["user_id"],       ["id"], "CASCADE"),
        ("profile_views",    "profile_views_viewer_id_fkey",     "users",    ["viewer_id"],     ["id"], "CASCADE"),
        ("profile_views",    "profile_views_viewed_id_fkey",     "users",    ["viewed_id"],     ["id"], "CASCADE"),
        ("quiz_answers",     "quiz_answers_user_id_fkey",        "users",    ["user_id"],       ["id"], "CASCADE"),
        ("politeness_votes", "politeness_votes_voter_id_fkey",   "users",    ["voter_id"],      ["id"], "CASCADE"),
        ("politeness_votes", "politeness_votes_target_id_fkey",  "users",    ["target_id"],     ["id"], "CASCADE"),
        ("profile_photos",   "profile_photos_profile_id_fkey",   "profiles", ["profile_id"],    ["id"], "CASCADE"),
        ("users",            "users_referred_by_id_fkey",        "users",    ["referred_by_id"],["id"], "SET NULL"),
    ]

    insp = _insp()

    for table, fk_name, ref_table, local_cols, remote_cols, on_delete in pairs:
        if not _table_exists(table):
            _log.debug("003: table %s not found, skip FK", table)
            continue
        try:
            existing = {fk["name"] for fk in insp.get_foreign_keys(table)}
            if fk_name in existing:
                op.drop_constraint(fk_name, table, type_="foreignkey")
            op.create_foreign_key(
                None, table, ref_table, local_cols, remote_cols, ondelete=on_delete
            )
        except Exception as exc:
            _log.warning("003: FK %s on %s skipped: %s", fk_name, table, exc)


def downgrade() -> None:
    for name, table in [
        ("ix_like_liked_is_like",   "likes"),
        ("ix_profile_swipe",        "profiles"),
        ("ix_user_last_seen",       "users"),
        ("ix_story_expires_at",     "stories"),
        ("ix_profile_view_viewed",  "profile_views"),
    ]:
        try:
            if _index_exists(name, table):
                op.drop_index(name, table_name=table)
        except Exception:
            pass

    try:
        if _column_exists("users", "token_version"):
            op.drop_column("users", "token_version")
    except Exception:
        pass

    try:
        if _unique_exists("stories", "uq_story_per_user"):
            op.drop_constraint("uq_story_per_user", "stories", type_="unique")
    except Exception:
        pass

    if _is_pg():
        try:
            op.drop_constraint("ck_match_user_order", "matches", type_="check")
        except Exception:
            pass
