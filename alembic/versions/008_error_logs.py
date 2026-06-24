"""Add error_logs table."""
import sqlalchemy as sa
from alembic import op

revision = "008"
down_revision = "007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "error_logs",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("ts", sa.DateTime, nullable=False),
        sa.Column("method", sa.String(10), nullable=True),
        sa.Column("path", sa.String(500), nullable=True),
        sa.Column("exc_type", sa.String(200), nullable=True),
        sa.Column("exc_msg", sa.Text, nullable=True),
        sa.Column("traceback", sa.Text, nullable=True),
        sa.Column("user_agent", sa.String(500), nullable=True),
    )
    op.create_index("ix_error_logs_ts", "error_logs", ["ts"])


def downgrade() -> None:
    op.drop_index("ix_error_logs_ts", table_name="error_logs")
    op.drop_table("error_logs")
