"""Add status/admin_note/reviewed_at to reports table."""
import sqlalchemy as sa
from alembic import op

revision = "012"
down_revision = "011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("reports", sa.Column("status", sa.String(20), nullable=False, server_default="pending"))
    op.add_column("reports", sa.Column("admin_note", sa.String(300), nullable=True))
    op.add_column("reports", sa.Column("reviewed_at", sa.DateTime, nullable=True))


def downgrade() -> None:
    op.drop_column("reports", "reviewed_at")
    op.drop_column("reports", "admin_note")
    op.drop_column("reports", "status")
