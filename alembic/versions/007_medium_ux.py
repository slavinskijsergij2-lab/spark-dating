"""Medium UX features: photo messages, edit message, archived matches, notification prefs

Revision ID: 007
Revises: 006
Create Date: 2026-06-24
"""
from alembic import op
import sqlalchemy as sa

revision = "007"
down_revision = "006"
branch_labels = None
depends_on = None


def upgrade():
    # Chat: image messages
    op.add_column("messages", sa.Column("is_image", sa.Boolean, nullable=False, server_default="false"))
    # Chat: edited messages
    op.add_column("messages", sa.Column("edited_at", sa.DateTime, nullable=True))
    # Matches: archive (timestamp = when archived)
    op.add_column("matches", sa.Column("archived_at", sa.DateTime, nullable=True))
    # Users: notification preferences
    op.add_column("users", sa.Column("notif_matches", sa.Boolean, nullable=False, server_default="true"))
    op.add_column("users", sa.Column("notif_messages", sa.Boolean, nullable=False, server_default="true"))
    op.add_column("users", sa.Column("notif_likes", sa.Boolean, nullable=False, server_default="true"))


def downgrade():
    op.drop_column("messages", "is_image")
    op.drop_column("messages", "edited_at")
    op.drop_column("matches", "archived_at")
    op.drop_column("users", "notif_matches")
    op.drop_column("users", "notif_messages")
    op.drop_column("users", "notif_likes")
