"""Add password reset token fields to users

Revision ID: 006
Revises: 005
Create Date: 2026-06-24
"""
from alembic import op
import sqlalchemy as sa

revision = "006"
down_revision = "005"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column("password_reset_token", sa.String(100), nullable=True))
    op.add_column("users", sa.Column("password_reset_expires", sa.DateTime, nullable=True))
    op.create_index("ix_user_reset_token", "users", ["password_reset_token"])


def downgrade():
    op.drop_index("ix_user_reset_token", table_name="users")
    op.drop_column("users", "password_reset_expires")
    op.drop_column("users", "password_reset_token")
