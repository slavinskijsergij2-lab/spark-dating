"""add premium_until, referral_code, referred_by_id columns

Revision ID: 002
Revises: 001
Create Date: 2026-06-22
"""
from alembic import op
import sqlalchemy as sa


revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None


def _col_exists(table: str, column: str) -> bool:
    conn = op.get_context().connection
    cols = [c["name"] for c in sa.inspect(conn).get_columns(table)]
    return column in cols


def upgrade() -> None:
    if not _col_exists("users", "premium_until"):
        op.add_column("users", sa.Column("premium_until", sa.DateTime, nullable=True))

    if not _col_exists("users", "referral_code"):
        op.add_column("users", sa.Column("referral_code", sa.String(20), nullable=True))
        op.create_index("ix_users_referral_code", "users", ["referral_code"], unique=True)

    if not _col_exists("users", "referred_by_id"):
        op.add_column("users", sa.Column(
            "referred_by_id", sa.Integer,
            sa.ForeignKey("users.id"), nullable=True,
        ))


def downgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.drop_column("referred_by_id")
        batch.drop_index("ix_users_referral_code")
        batch.drop_column("referral_code")
        batch.drop_column("premium_until")
