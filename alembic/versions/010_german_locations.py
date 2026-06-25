"""Add german_locations table and geo columns to profiles."""
import sqlalchemy as sa
from alembic import op

revision = "010"
down_revision = "009"
branch_labels = None
depends_on = None


def upgrade():
    # ── New table: german_locations ───────────────────────────────────────────
    op.create_table(
        "german_locations",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("geonames_id", sa.Integer, unique=True, nullable=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("name_ascii", sa.String(200), nullable=True),
        sa.Column("bundesland", sa.String(100), nullable=False),
        sa.Column("landkreis", sa.String(200), nullable=True),
        sa.Column("location_type", sa.String(10), server_default="PPL"),
        sa.Column("population", sa.Integer, server_default="0"),
        sa.Column("lat", sa.Float, nullable=False),
        sa.Column("lon", sa.Float, nullable=False),
    )
    op.create_index("ix_german_loc_name_ascii", "german_locations", ["name_ascii"])
    op.create_index("ix_german_loc_lat_lon", "german_locations", ["lat", "lon"])
    op.create_index("ix_german_loc_bundesland", "german_locations", ["bundesland"])

    # ── New nullable columns on profiles (existing users keep working) ────────
    op.add_column(
        "profiles",
        sa.Column(
            "location_id",
            sa.Integer,
            sa.ForeignKey("german_locations.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column("profiles", sa.Column("lat", sa.Float, nullable=True))
    op.add_column("profiles", sa.Column("lon", sa.Float, nullable=True))


def downgrade():
    op.drop_column("profiles", "lon")
    op.drop_column("profiles", "lat")
    op.drop_column("profiles", "location_id")
    op.drop_index("ix_german_loc_bundesland", table_name="german_locations")
    op.drop_index("ix_german_loc_lat_lon", table_name="german_locations")
    op.drop_index("ix_german_loc_name_ascii", table_name="german_locations")
    op.drop_table("german_locations")
