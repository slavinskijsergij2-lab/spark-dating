"""Add reply_to_id and is_deleted to messages."""
import sqlalchemy as sa
from alembic import op

revision = "013"
down_revision = "012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("messages", sa.Column("reply_to_id", sa.Integer, sa.ForeignKey("messages.id", ondelete="SET NULL"), nullable=True))
    op.add_column("messages", sa.Column("is_deleted", sa.Boolean, nullable=False, server_default="false"))


def downgrade() -> None:
    op.drop_column("messages", "reply_to_id")
    op.drop_column("messages", "is_deleted")
