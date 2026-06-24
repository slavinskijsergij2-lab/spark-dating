"""Add Stripe customer/subscription fields to users

Revision ID: 005
Revises: 004
Create Date: 2026-06-24
"""
from alembic import op
import sqlalchemy as sa

revision = "005"
down_revision = "004"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column("stripe_customer_id", sa.String(100), nullable=True))
    op.add_column("users", sa.Column("stripe_subscription_id", sa.String(100), nullable=True))
    op.create_index("ix_user_stripe_customer", "users", ["stripe_customer_id"])
    op.create_index("ix_user_stripe_sub", "users", ["stripe_subscription_id"])


def downgrade():
    op.drop_index("ix_user_stripe_sub", table_name="users")
    op.drop_index("ix_user_stripe_customer", table_name="users")
    op.drop_column("users", "stripe_subscription_id")
    op.drop_column("users", "stripe_customer_id")
