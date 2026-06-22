"""initial schema

Revision ID: 001
Revises:
Create Date: 2026-06-21
"""
from alembic import op
import sqlalchemy as sa

revision = "001"
down_revision = None
branch_labels = None
depends_on = None

_gender = sa.Enum("male", "female", "other", name="genderenum")


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("hashed_password", sa.String(255), nullable=False),
        sa.Column("language", sa.String(10), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("politeness_score", sa.Float(), nullable=True),
        sa.Column("politeness_votes", sa.Integer(), nullable=True),
        sa.Column("is_verified", sa.Boolean(), nullable=True),
        sa.Column("verify_gesture", sa.String(50), nullable=True),
        sa.Column("last_seen", sa.DateTime(), nullable=True),
        sa.Column("email_verified", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("email_verify_token", sa.String(100), nullable=True),
        sa.Column("email_verify_created_at", sa.DateTime(), nullable=True),
        sa.Column("is_premium", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("boost_until", sa.DateTime(), nullable=True),
        sa.Column("premium_until", sa.DateTime(), nullable=True),
        sa.Column("referral_code", sa.String(20), nullable=True),
        sa.Column("referred_by_id", sa.Integer(), nullable=True),
        sa.Column("birth_date", sa.DateTime(), nullable=True),
        sa.Column("phone", sa.String(20), nullable=True),
        sa.Column("phone_verified", sa.Boolean(), nullable=False, server_default="false"),
        sa.ForeignKeyConstraint(["referred_by_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email", name="uq_users_email"),
        sa.UniqueConstraint("referral_code", name="uq_users_referral_code"),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)
    op.create_index("ix_users_email_verify_token", "users", ["email_verify_token"])
    op.create_index("ix_users_referral_code", "users", ["referral_code"])

    op.create_table(
        "profiles",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("age", sa.Integer(), nullable=False),
        sa.Column("gender", _gender, nullable=False),
        sa.Column("looking_for", _gender, nullable=True),
        sa.Column("city", sa.String(100), nullable=True),
        sa.Column("bio", sa.Text(), nullable=True),
        sa.Column("photo", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("intention", sa.String(20), nullable=True),
        sa.Column("interests", sa.String(500), nullable=True),
        sa.Column("is_anonymous", sa.Boolean(), nullable=False, server_default="false"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", name="uq_profiles_user_id"),
    )
    op.create_index("ix_profiles_id", "profiles", ["id"])

    op.create_table(
        "likes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("liker_id", sa.Integer(), nullable=False),
        sa.Column("liked_id", sa.Integer(), nullable=False),
        sa.Column("is_like", sa.Boolean(), nullable=False),
        sa.Column("is_super", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["liked_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["liker_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("liker_id", "liked_id", name="uq_like_pair"),
    )
    op.create_index("ix_likes_liker_id", "likes", ["liker_id"])
    op.create_index("ix_likes_liked_id", "likes", ["liked_id"])
    op.create_index("ix_like_liker_created", "likes", ["liker_id", "created_at"])

    op.create_table(
        "matches",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user1_id", sa.Integer(), nullable=False),
        sa.Column("user2_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("seen_by_user1", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("seen_by_user2", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("streak_days", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_streak_date", sa.DateTime(), nullable=True),
        sa.Column("user1_revealed", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("user2_revealed", sa.Boolean(), nullable=False, server_default="true"),
        sa.ForeignKeyConstraint(["user1_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["user2_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user1_id", "user2_id", name="uq_match_pair"),
    )
    op.create_index("ix_matches_user1_id", "matches", ["user1_id"])
    op.create_index("ix_matches_user2_id", "matches", ["user2_id"])

    op.create_table(
        "messages",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("match_id", sa.Integer(), nullable=False),
        sa.Column("sender_id", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("is_read", sa.Boolean(), nullable=True),
        sa.Column("is_voice", sa.Boolean(), nullable=False, server_default="false"),
        sa.ForeignKeyConstraint(["match_id"], ["matches.id"]),
        sa.ForeignKeyConstraint(["sender_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_messages_sender_id", "messages", ["sender_id"])
    op.create_index("ix_message_match_id", "messages", ["match_id", "id", "created_at"])
    op.create_index("ix_message_unread", "messages", ["match_id", "is_read", "sender_id"])

    op.create_table(
        "politeness_votes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("voter_id", sa.Integer(), nullable=False),
        sa.Column("target_id", sa.Integer(), nullable=False),
        sa.Column("stars", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["target_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["voter_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("voter_id", "target_id", name="uq_politeness_vote"),
    )
    op.create_index("ix_politeness_votes_voter_id", "politeness_votes", ["voter_id"])
    op.create_index("ix_politeness_votes_target_id", "politeness_votes", ["target_id"])

    op.create_table(
        "profile_photos",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("profile_id", sa.Integer(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["profile_id"], ["profiles.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_profile_photos_profile_id", "profile_photos", ["profile_id"])

    op.create_table(
        "blocks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("blocker_id", sa.Integer(), nullable=False),
        sa.Column("blocked_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["blocked_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["blocker_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("blocker_id", "blocked_id", name="uq_block"),
    )
    op.create_index("ix_blocks_blocker_id", "blocks", ["blocker_id"])
    op.create_index("ix_blocks_blocked_id", "blocks", ["blocked_id"])

    op.create_table(
        "reports",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("reporter_id", sa.Integer(), nullable=False),
        sa.Column("reported_id", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(50), nullable=False),
        sa.Column("comment", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["reported_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["reporter_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("reporter_id", "reported_id", name="uq_report"),
    )
    op.create_index("ix_reports_reporter_id", "reports", ["reporter_id"])
    op.create_index("ix_reports_reported_id", "reports", ["reported_id"])

    op.create_table(
        "stories",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("media_type", sa.String(10), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_stories_user_id", "stories", ["user_id"])

    op.create_table(
        "profile_views",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("viewer_id", sa.Integer(), nullable=False),
        sa.Column("viewed_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["viewed_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["viewer_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("viewer_id", "viewed_id", name="uq_profile_view"),
    )
    op.create_index("ix_profile_views_viewer_id", "profile_views", ["viewer_id"])
    op.create_index("ix_profile_views_viewed_id", "profile_views", ["viewed_id"])

    op.create_table(
        "message_reactions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("message_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("emoji", sa.String(10), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["message_id"], ["messages.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("message_id", "user_id", name="uq_msg_reaction"),
    )
    op.create_index("ix_message_reactions_message_id", "message_reactions", ["message_id"])
    op.create_index("ix_message_reactions_user_id", "message_reactions", ["user_id"])

    op.create_table(
        "quiz_answers",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("question_id", sa.Integer(), nullable=False),
        sa.Column("answer_index", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "question_id", name="uq_quiz_answer"),
    )
    op.create_index("ix_quiz_answers_user_id", "quiz_answers", ["user_id"])


def downgrade() -> None:
    op.drop_table("quiz_answers")
    op.drop_table("message_reactions")
    op.drop_table("profile_views")
    op.drop_table("stories")
    op.drop_table("reports")
    op.drop_table("blocks")
    op.drop_table("profile_photos")
    op.drop_table("politeness_votes")
    op.drop_table("messages")
    op.drop_table("matches")
    op.drop_table("likes")
    op.drop_table("profiles")
    op.drop_table("users")
    _gender.drop(op.get_bind(), checkfirst=True)
