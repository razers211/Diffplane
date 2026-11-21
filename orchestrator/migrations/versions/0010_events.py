"""State-change events for the timeline: links, peers, hardware, alerts, drift and health.

Revision ID: 0010
Revises: 0009
"""

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "events",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("site", sa.String(100), index=True),
        sa.Column("device", sa.String(100), index=True),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("subject", sa.String(200), nullable=False),
        sa.Column("state", sa.String(60), nullable=False),
        sa.Column("previous", sa.String(60)),
        sa.Column("severity", sa.String(12), nullable=False),
        sa.Column("detail", sa.Text),
    )


def downgrade() -> None:
    op.drop_table("events")
