"""Add missing indexes, UniqueConstraint on stories, token_version, ON DELETE CASCADE (PG only)

Revision ID: 003
Revises: 002
Create Date: 2026-06-22
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.engine.reflection import Inspector

revision = "003"
down_revision = "002"
branch_labels = None
depends_on = None


def _is_pg() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def _index_exists(name: str, table: str) -> bool:
    bind = op.get_bind()
    insp = Inspector.from_engine(bind)
    return any(idx["name"] == name for idx in insp.get_indexes(table))


def _column_exists(table: str, column: str) -> bool:
    bind = op.get_bind()
    insp = Inspector.from_engine(bind)
    return any(c["name"] == column for c in insp.get_columns(table))


def _unique_exists(table: str, name: str) -> bool:
    bind = op.get_bind()
    insp = Inspector.from_engine(bind)
    return any(u["name"] == name for u in insp.get_unique_constraints(table))


def upgrade() -> None:
    # ── new indexes (work on both SQLite and PostgreSQL) ──────────────────────
    if not _index_exists("ix_like_liked_is_like", "likes"):
        op.create_index("ix_like_liked_is_like", "likes", ["liked_id", "is_like"])
    if not _index_exists("ix_profile_swipe", "profiles"):
        op.create_index("ix_profile_swipe", "profiles", ["gender", "age", "intention"])
    if not _index_exists("ix_user_last_seen", "users"):
        op.create_index("ix_user_last_seen", "users", ["last_seen"])
    if not _index_exists("ix_story_expires_at", "stories"):
        op.create_index("ix_story_expires_at", "stories", ["expires_at"])
    if not _index_exists("ix_profile_view_viewed", "profile_views"):
        op.create_index("ix_profile_view_viewed", "profile_views",
                        ["viewed_id", "viewer_id", "created_at"])

    # ── token_version column ──────────────────────────────────────────────────
    if not _column_exists("users", "token_version"):
        op.add_column("users", sa.Column(
            "token_version", sa.Integer(), nullable=False, server_default="0"
        ))

    # ── story: one story per user ─────────────────────────────────────────────
    # First remove duplicate rows (keep the newest per user)
    if _is_pg():
        op.execute("""
            DELETE FROM stories
            WHERE id NOT IN (
                SELECT MAX(id) FROM stories GROUP BY user_id
            )
        """)
    else:
        op.execute("""
            DELETE FROM stories
            WHERE id NOT IN (
                SELECT MAX(id) FROM stories GROUP BY user_id
            )
        """)

    # SQLite does not support ALTER TABLE ADD CONSTRAINT — skip on SQLite
    if _is_pg() and not _unique_exists("stories", "uq_story_per_user"):
        op.create_unique_constraint("uq_story_per_user", "stories", ["user_id"])

    # ── check constraint: user1_id < user2_id in matches (PostgreSQL only) ────
    if _is_pg():
        try:
            op.create_check_constraint(
                "ck_match_user_order", "matches", "user1_id < user2_id"
            )
        except Exception:
            pass  # already exists

    # ── clean up old expired story rows ──────────────────────────────────────
    if _is_pg():
        op.execute("DELETE FROM stories WHERE expires_at < NOW() - INTERVAL '1 day'")
    else:
        op.execute("DELETE FROM stories WHERE expires_at < datetime('now', '-1 day')")

    # ── ON DELETE CASCADE (PostgreSQL only — SQLite ignores FK constraints) ───
    if _is_pg():
        _recreate_fks()


def _recreate_fks() -> None:
    """Drop and recreate foreign keys with ON DELETE CASCADE on PostgreSQL."""
    pairs = [
        ("profiles",         "profiles_user_id_fkey",            "users",    ["user_id"],      ["id"], "CASCADE"),
        ("likes",            "likes_liker_id_fkey",              "users",    ["liker_id"],     ["id"], "CASCADE"),
        ("likes",            "likes_liked_id_fkey",              "users",    ["liked_id"],     ["id"], "CASCADE"),
        ("matches",          "matches_user1_id_fkey",            "users",    ["user1_id"],     ["id"], "CASCADE"),
        ("matches",          "matches_user2_id_fkey",            "users",    ["user2_id"],     ["id"], "CASCADE"),
        ("messages",         "messages_match_id_fkey",           "matches",  ["match_id"],     ["id"], "CASCADE"),
        ("messages",         "messages_sender_id_fkey",          "users",    ["sender_id"],    ["id"], "CASCADE"),
        ("message_reactions","message_reactions_message_id_fkey","messages", ["message_id"],   ["id"], "CASCADE"),
        ("message_reactions","message_reactions_user_id_fkey",   "users",    ["user_id"],      ["id"], "CASCADE"),
        ("blocks",           "blocks_blocker_id_fkey",           "users",    ["blocker_id"],   ["id"], "CASCADE"),
        ("blocks",           "blocks_blocked_id_fkey",           "users",    ["blocked_id"],   ["id"], "CASCADE"),
        ("reports",          "reports_reporter_id_fkey",         "users",    ["reporter_id"],  ["id"], "CASCADE"),
        ("reports",          "reports_reported_id_fkey",         "users",    ["reported_id"],  ["id"], "CASCADE"),
        ("stories",          "stories_user_id_fkey",             "users",    ["user_id"],      ["id"], "CASCADE"),
        ("profile_views",    "profile_views_viewer_id_fkey",     "users",    ["viewer_id"],    ["id"], "CASCADE"),
        ("profile_views",    "profile_views_viewed_id_fkey",     "users",    ["viewed_id"],    ["id"], "CASCADE"),
        ("quiz_answers",     "quiz_answers_user_id_fkey",        "users",    ["user_id"],      ["id"], "CASCADE"),
        ("politeness_votes", "politeness_votes_voter_id_fkey",   "users",    ["voter_id"],     ["id"], "CASCADE"),
        ("politeness_votes", "politeness_votes_target_id_fkey",  "users",    ["target_id"],    ["id"], "CASCADE"),
        ("profile_photos",   "profile_photos_profile_id_fkey",   "profiles", ["profile_id"],   ["id"], "CASCADE"),
        ("users",            "users_referred_by_id_fkey",        "users",    ["referred_by_id"],["id"],"SET NULL"),
    ]

    bind = op.get_bind()
    insp = Inspector.from_engine(bind)

    for table, fk_name, ref_table, local_cols, remote_cols, on_delete in pairs:
        existing = {fk["name"] for fk in insp.get_foreign_keys(table)}
        if fk_name in existing:
            op.drop_constraint(fk_name, table, type_="foreignkey")
        op.create_foreign_key(
            None, table, ref_table, local_cols, remote_cols, ondelete=on_delete
        )


def downgrade() -> None:
    for name, table in [
        ("ix_like_liked_is_like",   "likes"),
        ("ix_profile_swipe",        "profiles"),
        ("ix_user_last_seen",       "users"),
        ("ix_story_expires_at",     "stories"),
        ("ix_profile_view_viewed",  "profile_views"),
    ]:
        if _index_exists(name, table):
            op.drop_index(name, table_name=table)

    if _column_exists("users", "token_version"):
        op.drop_column("users", "token_version")

    if _unique_exists("stories", "uq_story_per_user"):
        op.drop_constraint("uq_story_per_user", "stories", type_="unique")

    if _is_pg():
        try:
            op.drop_constraint("ck_match_user_order", "matches", type_="check")
        except Exception:
            pass
