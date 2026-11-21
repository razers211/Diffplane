"""Intent documents: who wrote each version (an account, or "platform" for the platform itself).

Revision ID: 0017
Revises: 0016
"""

import sqlalchemy as sa
from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("intent_documents", sa.Column("written_by", sa.String(100), nullable=True))


def downgrade() -> None:
    op.drop_column("intent_documents", "written_by")
