"""Upgrade jobs' images.

Revision ID: 0013
Revises: 0012
"""

import sqlalchemy as sa
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("params", sa.JSON, nullable=True))


def downgrade() -> None:
    op.drop_column("jobs", "params")
