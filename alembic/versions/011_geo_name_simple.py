"""Add name_simple to german_locations for umlaut-free search (ö→o, ü→u, ä→a).

Revision ID: 011
Revises: 010
Create Date: 2026-06-25
"""
import sqlalchemy as sa
from alembic import op

revision = "011"
down_revision = "010"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "german_locations",
        sa.Column("name_simple", sa.String(200), nullable=True),
    )
    op.create_index("ix_german_loc_name_simple", "german_locations", ["name_simple"])

    # Populate: simple transliteration (ä→a ö→o ü→u Ä→A Ö→O Ü→U ß→ss)
    op.execute(
        """
        UPDATE german_locations
        SET name_simple = LOWER(
            REPLACE(
                TRANSLATE(name, 'äöüÄÖÜ', 'aouAOU'),
                'ß', 'ss'
            )
        )
        """
    )


def downgrade():
    op.drop_index("ix_german_loc_name_simple", table_name="german_locations")
    op.drop_column("german_locations", "name_simple")
