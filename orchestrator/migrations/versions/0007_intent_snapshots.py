"""Intent snapshots: a fabric's GUI-written intent when one of its deploys was verified.

Revision ID: 0007
Revises: 0006
"""

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "intent_snapshots",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("site", sa.String(100), nullable=False, index=True),
        sa.Column("job_id", sa.Integer),
        sa.Column("taken_at", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("digest", sa.String(64), nullable=False),
        sa.Column("data", sa.JSON, nullable=False),
    )


def downgrade() -> None:
    op.drop_table("intent_snapshots")
