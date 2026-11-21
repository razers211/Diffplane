"""Pre-deploy checks kept with a deploy job.

Revision ID: 0008
Revises: 0007
"""

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("checks", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("jobs", "checks")
