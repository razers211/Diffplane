"""Switch config versions: each switch's running config, encrypted, with why it was taken.

Revision ID: 0006
Revises: 0005
"""

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "config_versions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("device", sa.String(100), nullable=False, index=True),
        sa.Column("site", sa.String(100)),
        sa.Column("taken_at", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("reason", sa.String(20), nullable=False),
        sa.Column("job_id", sa.Integer),
        sa.Column("requested_by", sa.String(100)),
        sa.Column("digest", sa.String(64), nullable=False),
        sa.Column("lines", sa.Integer, nullable=False),
        sa.Column("config", sa.LargeBinary, nullable=False),
    )


def downgrade() -> None:
    op.drop_table("config_versions")
