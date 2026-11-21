"""Hourly traffic per port and trunk for capacity trends.

Revision ID: 0011
Revises: 0010
"""

import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "traffic_hourly",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("device", sa.String(100), nullable=False),
        sa.Column("interface", sa.String(100), nullable=False),
        sa.Column("direction", sa.String(3), nullable=False),
        sa.Column("hour", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("avg_bps", sa.Float),
        sa.Column("p95_bps", sa.Float),
        sa.UniqueConstraint("device", "interface", "direction", "hour"),
    )


def downgrade() -> None:
    op.drop_table("traffic_hourly")
