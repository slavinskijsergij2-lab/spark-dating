"""Add play_purchases table for Google Play Billing (Android TWA)."""
import sqlalchemy as sa

from alembic import op

revision = "014"
down_revision = "013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "play_purchases",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("purchase_token", sa.String(512), nullable=False, unique=True),
        sa.Column("product_id", sa.String(100), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("expires_at", sa.DateTime, nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=True),
    )
    op.create_index("ix_play_purchases_user_id", "play_purchases", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_play_purchases_user_id", table_name="play_purchases")
    op.drop_table("play_purchases")
