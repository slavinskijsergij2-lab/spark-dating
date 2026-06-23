"""Add push_subscriptions table for Web Push / PWA notifications

Revision ID: 004
Revises: 003
Create Date: 2026-06-23
"""
from alembic import op
import sqlalchemy as sa


def upgrade():
    op.create_table(
        "push_subscriptions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("endpoint", sa.Text(), nullable=False, unique=True),
        sa.Column("p256dh", sa.Text(), nullable=False),
        sa.Column("auth", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_push_sub_user", "push_subscriptions", ["user_id"])


def downgrade():
    op.drop_index("ix_push_sub_user", table_name="push_subscriptions")
    op.drop_table("push_subscriptions")
